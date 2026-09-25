-- enqueue.lua - atomically place a session in the queue (idempotent)
-- KEYS[1] = queue sorted set
-- KEYS[2] = session hash
-- KEYS[3] = kill switch key
-- KEYS[4] = last-seen sorted set (member=session_id, score=unix seconds)
-- KEYS[5] = admitted sorted set
-- ARGV[1] = session id
-- ARGV[2] = score (enqueue timestamp, seconds)
-- ARGV[3] = session ttl seconds
-- ARGV[4] = now (unix seconds)
-- ARGV[5] = client ip
-- ARGV[6] = client user-agent hash
-- ARGV[7] = "1" to refuse reusing a session created by a different client
-- ARGV[8..]  = alternating hash field/value pairs for a new session
-- Returns: 1-indexed position; 0 if the session is already admitted;
--          -1 if the kill switch is engaged; -2 if the session id belongs to
--          another client (caller should start a fresh session).

if redis.call('GET', KEYS[3]) == '1' then
  return -1
end

local sid = ARGV[1]
local ttl = tonumber(ARGV[3])

if redis.call('EXISTS', KEYS[2]) == 1 then
  if ARGV[7] == '1' then
    local owner = redis.call('HMGET', KEYS[2], 'ip', 'ua')
    if owner[1] ~= ARGV[5] or owner[2] ~= ARGV[6] then
      return -2
    end
  end
  if redis.call('ZSCORE', KEYS[5], sid) then
    return 0
  end
  local rank = redis.call('ZRANK', KEYS[1], sid)
  if rank ~= false then
    -- Returning waiter: keep their place and metadata, just mark them alive.
    redis.call('ZADD', KEYS[4], ARGV[4], sid)
    redis.call('EXPIRE', KEYS[2], ttl)
    return rank + 1
  end
end

-- New session, or a known one whose admission lapsed: (re)join at the back.
redis.call('ZADD', KEYS[1], 'NX', ARGV[2], sid)
redis.call('ZADD', KEYS[4], ARGV[4], sid)
local hash_args = {}
for i = 8, #ARGV do
  table.insert(hash_args, ARGV[i])
end
if #hash_args > 0 then
  redis.call('HSET', KEYS[2], unpack(hash_args))
end
redis.call('EXPIRE', KEYS[2], ttl)

return redis.call('ZRANK', KEYS[1], sid) + 1
