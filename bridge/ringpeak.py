import mmap,struct,time
RING=16+16384*2*4
f=open('/dev/shm/norns-bridge-v2','rb');m=mmap.mmap(f.fileno(),0,access=mmap.ACCESS_READ)
def peak(base,n=9600):
    w=struct.unpack_from('<Q',m,base)[0]
    best=0.0
    for i in range(w-n,w):
        l,r=struct.unpack_from('<ff',m,base+16+(i&16383)*8)
        best=max(best,abs(l),abs(r))
    return best
for _ in range(3):
    print('to_norns (Machinista sidechain) peak %.4f   to_machinista (norns out) peak %.4f'%(peak(RING),peak(0)));time.sleep(1)
