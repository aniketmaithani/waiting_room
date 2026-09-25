-- reclaim.lua - drop idle queued sessions and expired admissions
-- KEYS[1] = queue sorted set
-- KEYS[2] = admitted sorted set
-- KEYS[3] = last-seen sorted set
-- ARGV[1] = idle cutoff (unix seconds): queued sessions not seen since are dropped
-- ARGV[2] = admitted cutoff (ms): admissions whose slot deadline passed are freed
-- ARGV[3] = session hash key prefix (e.g. "wr:{room}:session:")
-- Returns: total reclaimed count.

local idle = redis.call('ZRANGEBYSCORE', KEYS[3], '-inf', ARGV[1])
local q = 0
for _, sid in ipairs(idle) do
  q = q + redis.call('ZREM', KEYS[1], sid)
  redis.call('DEL', ARGV[3] .. sid)
end
redis.call('ZREMRANGEBYSCORE', KEYS[3], '-inf', ARGV[1])

local expired = redis.call('ZRANGEBYSCORE', KEYS[2], '-inf', ARGV[2])
for _, sid in ipairs(expired) do
  if redis.call('EXISTS', ARGV[3] .. sid) == 1 then
    redis.call('HSET', ARGV[3] .. sid, 'state', 'expired')
  end
end
local a = redis.call('ZREMRANGEBYSCORE', KEYS[2], '-inf', ARGV[2])
return q + a
