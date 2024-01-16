# -*- coding: utf-8 -*-
"""
Check sync of clock against a time server.

Requirements: one computer (at SERVER_ADDRESS) on the network set up as a time server, replying to NTP queries.
"""

import os
import platform
import datetime
import ntplib   # NB: local modified copy of ntplib
import win_precise_time as wpt
import argparse

SERVER_ADDRESS = '10.0.1.251'

def main(args):
    # Change directory
    this_dir = os.path.abspath(os.path.dirname(__file__))
    os.chdir(this_dir)

    c = ntplib.NTPClient() # Get a connection to the time client

    # Create text file and write header
    t = datetime.datetime.now()
    f_name = platform.node()+('_'.join([args.run_nr,str(t.year),str(t.month),str(t.day),
                       str(t.hour),str(t.minute),str(t.second)]))+'.tsv'
    f = open(f_name,'w')
    f.write('%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' %
            ('iteration','time(ms)','offset(ms)','delay(ms)',
             't_client_sent','t_server_received',
             't_server_sent', 't_client_received'))

    #--------------------------------------------------------------------------
    # Check how well the clocks are synched
    #--------------------------------------------------------------------------
    t = wpt.time() # Reset clock

    try:
        for i in range(args.iterations):
            if i>0:
                wpt.sleep(args.wait)
            for _ in range(args.n_sync):
                try:
                    response = c.request(SERVER_ADDRESS, timeout=0.1)
                    offset = response.offset  # Offset between server time and local time
                    delay = response.delay    # Round-trip latency

                    t_client_sent       = response.orig_timestamp
                    t_server_received   = response.recv_timestamp
                    t_server_sent       = response.tx_timestamp
                    t_client_received   = response.dest_timestamp

                    t_ms = (wpt.time() - t)*1000

                    # Write results to text file
                    f.write('%d\t%.2f\t%.2f\t%.2f\t%.4f\t%.4f\t%.4f\t%.4f\n' %
                            (i,t_ms,offset*1000,delay*1000,
                            t_client_sent,t_server_received,t_server_sent,
                            t_client_received))

                except:
                    print("no response")
                    continue
    except KeyboardInterrupt:
        # allow interruptions by keyboard, store what we have up till now
        pass

    f.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="check_clock_sync")
    parser.add_argument('-n','--n_sync', type=int, default=10, help="number of times to check sync for each iteration")
    parser.add_argument('-i','--iterations', type=int, default=1, help="number of iterations")
    parser.add_argument('-w','--wait', type=int, default=5, help="sleep time between iterations (seconds)")
    parser.add_argument('-r','--run_nr', type=str, default=str(0), help="run sequence number")

    args = parser.parse_args()
    main(args)
