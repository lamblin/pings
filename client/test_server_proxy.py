"""Code snippet to test the Java ServerProxy interface to the Pings server."""
import sys
import time

import ClientInfo
import ServerProxy

nb_iter = 1000
server = "iconnect.iro.umontreal.ca"
port = 80

print sys.argv
if len(sys.argv) > 1:
    server = sys.argv[1]
if len(sys.argv) > 2:
    port = int(sys.argv[2])
print server, port
sp = ServerProxy(server, port)


if False:
    # Need to change permissions on ServerProxy Java class for this to work.
    print 'Calling doJsonRequest directly...'
    r = sp.doJsonRequest('/get_pings', None)
    print r

print

info = ClientInfo()
info.setNickname("yoda")

pings_queue = [None] * nb_iter
t0 = time.time()
for i in range(nb_iter):
    pings_queue[i] = sp.getPings(info)
t1 = time.time()
print "Time get_results r/s:",  nb_iter / (t1 - t0)

if True:
    for i in range(nb_iter):
        pings = pings_queue[i]
        #print 'Client Geoip', info.getGeoipInfo()
        #print 'Token', pings.token
        #print 'First address', pings.addresses[0]
        #print 'Geoip for first address', pings.geoip_info[0]

# Fill in results.
for it in range(len(pings_queue)):
    pings = pings_queue[it]
    for i in range(len(pings.addresses)):
        pings.results[i] = 'FOO %d' % (i * it)

print
print 'Submitting results'
try:
    try:
        t0 = time.time()
        for i in range(nb_iter):
            sp.submitResults(info, pings_queue[i])
    except Exception, e:
        print e
finally:
    t1 = time.time()

print "Time submitResults r/s:",  nb_iter / (t1 - t0)
