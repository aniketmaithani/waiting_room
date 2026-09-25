-- position.lua - read a session's queue position and record that it is alive
-- KEYS[1] = queue sorted set
-- KEYS[2] = admitted sorted set
-- KEYS[3] = last-seen sorted set
-- KEYS[4] = session hash
-- KEYS[5] = kill switch key
-- ARGV[1] = session id
-- ARGV[2] = now (unix seconds)
-- ARGV[3] = session ttl seconds (0 = read only, do not refresh liveness)
-- Returns: {position, size, admitted, closed}. position is 1-indexed, 0 if not
-- queued; admitted is 1 when the session currently holds an admission slot;
-- closed is 1 while the kill switch is engaged.

local rank = redis.call('ZRANK', KEYS[1], ARGV[1])
local size = redis.call('ZCARD', KEYS[1])
local closed = redis.call('EXISTS', KEYS[5])
if rank == false then
  local admitted = 0
  if redis.call('ZSCORE', KEYS[2], ARGV[1]) then
    admitted = 1
  end
  return { 0, size, admitted, closed }
end

local ttl = tonumber(ARGV[3])
if ttl > 0 then
  redis.call('ZADD', KEYS[3], ARGV[2], ARGV[1])
  redis.call('EXPIRE', KEYS[4], ttl)
end
return { rank + 1, size, 0, closed }
