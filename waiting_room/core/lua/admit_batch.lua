-- admit_batch.lua - atomically admit sessions from the front of the queue
--
-- Every admission limit is enforced here, inside one script, so concurrent
-- ticks from any number of processes can never overshoot the rate or the
-- capacity of a room.
--
-- KEYS[1] = queue sorted set
-- KEYS[2] = admitted sorted set (member=session_id, score=slot deadline ms)
-- KEYS[3] = token bucket hash (fields: tokens, ts)
-- KEYS[4] = kill switch key
-- ARGV[1] = max sessions to admit this tick
-- ARGV[2] = now (ms)
-- ARGV[3] = grace window (ms) an admitted session holds its slot before redeeming
-- ARGV[4] = session hash key prefix (e.g. "wr:{room}:session:")
-- ARGV[5] = capacity (max concurrent admitted sessions, 0 = unlimited)
-- ARGV[6] = admission rate per second (0 = unlimited)
-- ARGV[7] = burst (token bucket size)
-- Returns: list of admitted session ids (possibly empty).

local max_n = tonumber(ARGV[1])
local now = tonumber(ARGV[2])
local grace = tonumber(ARGV[3])
local prefix = ARGV[4]
local capacity = tonumber(ARGV[5])
local rate = tonumber(ARGV[6])
local burst = tonumber(ARGV[7])

-- 1. Free slots whose deadline has passed so capacity recovers without a cron job.
local expired = redis.call('ZRANGEBYSCORE', KEYS[2], '-inf', now)
if #expired > 0 then
  redis.call('ZREMRANGEBYSCORE', KEYS[2], '-inf', now)
  for _, sid in ipairs(expired) do
    if redis.call('EXISTS', prefix .. sid) == 1 then
      redis.call('HSET', prefix .. sid, 'state', 'expired')
    end
  end
end

-- 2. A closed room admits nobody.
if redis.call('EXISTS', KEYS[4]) == 1 then
  return {}
end

if max_n == nil or max_n <= 0 then
  return {}
end
local n = math.min(max_n, redis.call('ZCARD', KEYS[1]))
if n <= 0 then
  return {}
end

-- 3. Capacity: never exceed the configured number of concurrent admissions.
if capacity > 0 then
  n = math.min(n, capacity - redis.call('ZCARD', KEYS[2]))
end

-- 4. Rate: shared token bucket, refilled from elapsed wall-clock time.
local tokens = 0
local bucket_size = 0
if rate > 0 then
  bucket_size = math.max(burst, rate, 1)
  local state = redis.call('HMGET', KEYS[3], 'tokens', 'ts')
  tokens = tonumber(state[1])
  local ts = tonumber(state[2])
  if tokens == nil or ts == nil then
    tokens = bucket_size
    ts = now
  end
  local elapsed = math.max(0, now - ts) / 1000.0
  tokens = math.min(bucket_size, tokens + elapsed * rate)
  n = math.min(n, math.floor(tokens))
end

-- 5. Pop and admit. Sessions whose metadata already expired are dropped.
local admitted = {}
if n > 0 then
  local members = redis.call('ZRANGE', KEYS[1], 0, n - 1)
  if #members > 0 then
    redis.call('ZREM', KEYS[1], unpack(members))
    local zadd_args = {}
    for _, sid in ipairs(members) do
      local key = prefix .. sid
      if redis.call('EXISTS', key) == 1 then
        redis.call('HSET', key, 'state', 'admitted', 'admitted_at', now)
        table.insert(zadd_args, now + grace)
        table.insert(zadd_args, sid)
        table.insert(admitted, sid)
      end
    end
    if #zadd_args > 0 then
      redis.call('ZADD', KEYS[2], unpack(zadd_args))
    end
  end
end

-- 6. Persist the bucket even when nothing was admitted so elapsed time is not
--    counted twice. It expires once it would have refilled completely anyway.
if rate > 0 then
  redis.call('HSET', KEYS[3], 'tokens', tokens - #admitted, 'ts', now)
  redis.call('PEXPIRE', KEYS[3], math.ceil(bucket_size / rate * 1000) + 60000)
end

return admitted
