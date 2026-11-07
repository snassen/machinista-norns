#include <jack/jack.h>
#include <stdio.h>
int main(int c,char**v){if(c!=3)return 2;jack_client_t*j=jack_client_open("jc",JackNoStartServer,0);if(!j)return 1;int r=jack_connect(j,v[1],v[2]);jack_client_close(j);return r&&r!=17;}
