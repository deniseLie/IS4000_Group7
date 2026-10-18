"""In-process token bucket (architecture §5.3; Redis was removed). Limits hold per process only: several workers or
replicas each keep their own buckets, and a restart refills them."""
import math
import threading
import time

MAX_KEYS=10000


class TokenBucket:
    def __init__(self,capacity,refill_per_s,clock=time.monotonic):
        self.capacity,self.refill,self.clock=capacity,refill_per_s,clock
        self.buckets={}
        self.lock=threading.Lock()

    def take(self,key):
        """0 when allowed, else whole seconds until one token is back (for Retry-After)."""
        with self.lock:
            now=self.clock()
            tokens,last=self.buckets.get(key,(self.capacity,now))
            tokens=min(self.capacity,tokens+(now-last)*self.refill)
            if tokens<1:
                self.buckets[key]=(tokens,now)
                return max(1,math.ceil((1-tokens)/self.refill))
            self.buckets[key]=(tokens-1,now)
            if len(self.buckets)>MAX_KEYS:self._prune(now)
            return 0

    def _prune(self,now):
        full=[k for k,(t,last) in self.buckets.items() if t+(now-last)*self.refill>=self.capacity]
        for k in full:del self.buckets[k]
