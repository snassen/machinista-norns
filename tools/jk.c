#include <jack/jack.h>
#include <stdio.h>
int main(void){jack_client_t*c=jack_client_open("jk",JackNoStartServer,0);if(!c)return 1;const char**p=jack_get_ports(c,0,0,0);for(int i=0;p&&p[i];i++)puts(p[i]);printf("jack cpu load %.1f\n",jack_cpu_load(c));jack_client_close(c);return 0;}
