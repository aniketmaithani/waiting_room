-- enqueue.lua - atomically place a session in the queue
-- KEYS[1] = queue sorted set
-- KEYS[2] = session hash
-- KEYS[3] = kill switch key
-- ARGV[1] = session id
-- ARGV[2] = score (enqueue timestamp, ms)
-- ARGV[3] = session ttl seconds
-- ARGV[4..]  = alternating hash field/value pairs
-- Returns: 1-indexed position, or -1 if the kill switch is engaged.

if redis.call('GET', KEYS[3]) == '1' then
  return -1
end

redis.call('ZADD', KEYS[1], 'NX', ARGV[2], ARGV[1])

local hash_args = {}
for i = 4, #ARGV do
  table.insert(hash_args, ARGV[i])
end
if #hash_args > 0 then
  redis.call('HSET', KEYS[2], unpack(hash_args))
end
redis.call('EXPIRE', KEYS[2], tonumber(ARGV[3]))

local rank = redis.call('ZRANK', KEYS[1], ARGV[1])
if rank == false then
  return -1
end
return rank + 1
