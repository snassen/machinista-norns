#include <stdlib.h>
#include <jack/jack.h>
#include <math.h>
#include <stdio.h>
#include <unistd.h>
static jack_port_t *p[2]; static volatile float pk[2];
static int cb(jack_nframes_t n, void *a){(void)a;for(int c=0;c<2;c++){float *b=jack_port_get_buffer(p[c],n);for(jack_nframes_t i=0;i<n;i++){float v=fabsf(b[i]);if(v>pk[c])pk[c]=v;}}return 0;}
int main(int argc,char**argv){int secs=argc>1?atoi(argv[1]):5;jack_client_t*c=jack_client_open("peak",JackNoStartServer,0);if(!c){puts("PEAK_NO_JACK");return 1;}
p[0]=jack_port_register(c,"in1",JACK_DEFAULT_AUDIO_TYPE,JackPortIsInput,0);p[1]=jack_port_register(c,"in2",JACK_DEFAULT_AUDIO_TYPE,JackPortIsInput,0);jack_set_process_callback(c,cb,0);jack_activate(c);
int r1=jack_connect(c,"crone:output_1","peak:in1"),r2=jack_connect(c,"crone:output_2","peak:in2");printf("PEAK_CONNECT %d %d\n",r1,r2);
for(int i=0;i<secs;i++){sleep(1);printf("PEAK %.4f %.4f\n",pk[0],pk[1]);fflush(stdout);pk[0]=pk[1]=0;}jack_client_close(c);return 0;}
