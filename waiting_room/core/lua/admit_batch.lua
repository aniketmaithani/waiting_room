-- admit_batch.lua - atomically pop the front N sessions and mark them admitted
-- KEYS[1] = queue sorted set
-- KEYS[2] = admitted sorted set (member=session_id, score=grace_deadline_ms)
-- ARGV[1] = max sessions to admit
-- ARGV[2] = admitted_at (ms)
-- ARGV[3] = grace deadline (ms)
-- ARGV[4] = session hash key prefix (e.g. "wr:{room}:session:")
-- Returns: list of admitted session ids (possibly empty).

local n = tonumber(ARGV[1])
if n == nil or n <= 0 then
  return {}
end

local members = redis.call('ZRANGE', KEYS[1], 0, n - 1)
if #members == 0 then
  return {}
end

redis.call('ZREM', KEYS[1], unpack(members))

local zadd_args = {}
for _, sid in ipairs(members) do
  table.insert(zadd_args, ARGV[3])
  table.insert(zadd_args, sid)
  redis.call(
    'HSET',
    ARGV[4] .. sid,
    'state',
    'admitted',
    'admitted_at',
    ARGV[2]
  )
end
redis.call('ZADD', KEYS[2], unpack(zadd_args))

return members
