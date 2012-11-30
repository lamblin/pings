import logging
import os
import time

#We need pylibmc, not memcache or ultramemcache or umemcache
#We call token_mc.delete and only pylibmc return an error is the key was not then
#We rely on this behavior
import pylibmc as memcache
import ipaddr
import random
import pygeoip
from gevent_zeromq import zmq
from pings.web_server import leaderboards

logger = logging.getLogger(__name__)

# Warning: the use of a module-level variable for the zeromq sockets will
# NOT work if running this server with multiple threads. (You can't share
# zeromq sockets between threads.) This is not a problem because we are
# using gevent, which uses greenlets, not threads. But if ever changing to
# a WSGI server that uses threads, you will need to ensure that the zeromq
# socket is not accessed from threads other than the one which created it.

#
# Initialization
#

# Memcache instance for the security tokens.
token_mc = None
# Expiration time for the tokens in said memcache. If a client takes
# longer than this number of seconds to ping the list of addresses it was
# assigned and send back its reply, said reply will be refused when
# received.
token_exptime = None


def init_token_memcache(memcache_addresses, exptime):
    global token_mc, token_exptime
    token_mc = memcache.Client(memcache_addresses)
    token_exptime = exptime

#
# Zeromq resources
#

_zmq_context = None


def get_zmq_context():
    """Use this to retrieve the Zeromq context. It is created on demand if
    needed."""
    global _zmq_context
    if _zmq_context is None:
        _zmq_context = zmq.Context()
    return _zmq_context


# connection to the storage_server.
zmq_send_results_socket = None


def init_storage_zmq(zmq_urls):
    global zmq_send_results_socket
    zmq_send_results_socket = get_zmq_context().socket(zmq.PUSH)

    for url in zmq_urls:
        zmq_send_results_socket.connect(url)


# Zeromq sockets for leaderboards server
zmq_incr_score_socket = None


def init_rankings_zmq(incr_scores_url, publish_leaderboards_url):
    global zmq_incr_score_socket, leaderboards_proxy

    zmq_incr_score_socket = get_zmq_context().socket(zmq.PUSH)
    zmq_incr_score_socket.connect(incr_scores_url)

    leaderboards.init(get_zmq_context(), publish_leaderboards_url)

#
# GeoIP database
#

# GeoIP object
geoip = None


def init_geoip():
    """Initialize the GeoIP component. We expect the GeoLiteCity.dat file
    to be located in the directory containing the 'pings' Python package."""
    global geoip
    geoip = pygeoip.GeoIP(os.path.join(os.path.dirname(__file__),
                                       '..', '..', 'GeoLiteCity.dat'),
                          pygeoip.MEMORY_CACHE)


#
# Pyramid ressource class.
#

class Root(object):
    def __init__(self, request):
        self.request = request


#
# Functions that implement the low-level, core actions of the pings server.
#

# This is the total number of ip we try to pings
# The number of other clients is at max half that
#  none if less then 9 clients
#  and less then len(last_clients.list) * probability_to_ping
_num_addresses = 15


def init_web_service(num_addresses):
    global _num_addresses
    _num_addresses = num_addresses


def get_token():
    """Gets a security token. (A random base64 ascii string.)"""
    token = os.urandom(16).encode("base64")[:22]
    assert token_mc.set(token, True, token_exptime)
    return token


def check_token(token):
    """Checks that a given security token is still valid."""
    if token is None:
        return False

    # Lookup token in memcache.
    #
    # Memcache doesn't like getting Unicode for keys. And even though
    # get_token() returns a str, by the time it's sent to the client and
    # back, it will likely have been converted to Unicode (happening now,
    # at least). Convert the token back to a str here to handle this
    # problem in a localized way. Since the token is currently a base64
    # string, ascii is okay as an encoding. (If fed a token that can't be
    # converted to ASCII, Pyramid will convert the exception to a 500 error.)
    return token_mc.delete(token.encode('ascii')) is not None

# The address of the server that all clients should ping
always_up_addresses = ["173.194.73.104", "183.60.136.45", "195.22.144.60"]
# The probability that we ping an reference ip and used for the prob to
# ping past client ip.
probability_to_ping = 0.1


class active_queue:
    """Maintain a queue of a given maximum size s registering the last s
    elements that were added.Duplicates are automatically destroyed. Can be
    resized. Non thread-safe."""
    #A better implementation would use doubly linked list
    def __init__(self, size):
        self.by_address = {}
        self.list = []
        self.max_size = size

    def add(self, elem):
        if elem in self.by_address:
            self.list.remove(elem)
            self.list.append(elem)
        else:
            self.by_address[elem] = None
            if len(self.list) >= self.max_size:
                to_remove = self.list.pop(0)
                self.by_address.pop(to_remove)
            self.list.append(elem)

    def get_list(self):
        return list

    def resize(self, new_size):
        if new_size < self.size:
            self.list = self.list[len(self.list) - new_size:len(self.list)]
        self.size = new_size

    def get_random(self):
        return random.choice(self.list)

#Should larger than the current number of client connected but decreases
#efficiency if is too large
queue_size = 100
last_clients = active_queue(queue_size)


def get_int_from_ip(ip):
    int_parts = [int(str_) for str_ in ip.split('.')]
    return int_parts[3] + 256 * (int_parts[2] + 256 * (int_parts[1] + 256 * int_parts[0]))


def get_pings(client_addr):
    """Returns a list (of length 'num_addresses') of IP addresses to be
    pinged."""
    ip_addresses = []

    # Add some reference address to ping.
    for ip in always_up_addresses:
        if random.random() < probability_to_ping:
            ip_addresses.append(str(ip))

    # Pings other clients
    client_ip = get_int_from_ip(client_addr)
    # Do not ping clients when the cache is too low
    # Otherwise, this could cause a DDoS attack.
    if len(last_clients.list) > 9:
        # Add up to half of _num_addresses from other peers
        nb_cli_ip = _num_addresses / 2
        num_tries = 0
        # If too few past clients, lower the prob to ping them.
        max_tries = min(_num_addresses,
                        len(last_clients.list) * probability_to_ping)
        while len(ip_addresses) < nb_cli_ip and num_tries < max_tries:
            num_tries += 1
            try:
                random_ip = last_clients.get_random()
                # Don't ping ourself and don't ping twice the same ip
                if random_ip != client_ip and random_ip not in ip_addresses:
                    ip_addresses.append(random_ip)
            except Exception:
                pass
    ip_addresses = [str(ipaddr.IPv4Address(ip)) for ip in ip_addresses]

    # Add current client to the list of pingable clients.
    # Do not accept 127.0.0.1 as we can't get geo data
    # TODO: test for other ip that we do not have geo ip.
    #       and this cause a crash.
    if client_ip != 2130706433:
        last_clients.add(client_ip)

    # The num_tries < max_tries part of the loop is to guarantee that
    # this function executes in a bounded time.
    num_tries = 0
    max_tries = _num_addresses * 6
    while len(ip_addresses) < _num_addresses and num_tries < max_tries:
        num_tries += 1
        # Create a random IPv4 address. Exclude 0.0.0.0 and 255.255.255.255.
        ip = ipaddr.IPv4Address(random.randint(1, 2 ** 32 - 2))

        # Add address if it is a valid global IP address. (Addresses that
        # start with a leading first byte of 0 are also not valid
        # destination addresses, so filter them out too.)
#        if not (ip.is_link_local or ip.is_loopback or ip.is_multicast or
#                ip.is_private or ip.is_reserved or ip.is_unspecified
#                or ip.packed[0] == '\x00'):

        if not ((2851995648 <= ip._ip <= 28520611830) or  # is_link_local
                (2130706432 <= ip._ip <= 2147483647) or  # is_loopback
                (3758096384 <= ip._ip <= 4026531839) or  # is_multicast
                ((167772160 <= ip._ip <= 184549375) or
                 (2886729728 <= ip._ip <= 2887778303) or
                 (3232235520 <= ip._ip <= 3232301055)) or  # is_private
                (4026531840 <= ip._ip <= 4294967295) or  # is_reserved
                (ip._ip == 0) or  #  is_unspecified
                ip.packed[0] == '\x00'):
            ip_addresses.append(str(ip))

    return ip_addresses


def get_geoip_data(ip_addresses):
    """When passed a list of IP addresses (as strings), returns a list of
    dicts containing GeoIP data, one for each IP address passed."""
    results = []
    for address in ip_addresses:
        try:
            geoip_data = geoip.record_by_addr(address)

# pygeoip 0.2.2 need the following bug fix
#         0.2.3 crash when we load the data file
#         0.2.4 need the fix or the server err. With the fix 230 r/s
#         0.2.5 need the fix or the client crash. With the fix 210 r/s
            if geoip_data is not None:
                # The pygeoip library doesn't use Unicode for string values
                # and returns raw Latin-1 instead (at least for the free
                # GeoLiteCity.dat data). This makes other module (like json)
                # that expect Unicode to be used for non-ASCII characters
                # extremely unhappy. The relevant bug report is here:
                # https://github.com/appliedsec/pygeoip/issues/1
                #
                # As a workaround, convert all string values in returned
                # dict from Latin-1 to Unicode.
                for key, value in geoip_data.iteritems():
                    if isinstance(value, str):
                        geoip_data[key] = value.decode('latin-1')

        except Exception, e:
            logger.exception('Exception during lookup of geoip data ' +
                             'for IP address "%s".', address)
            geoip_data = None

        results.append(geoip_data)

    logger.debug('GeoIP results: %s', results)
    return results


def update_leaderboards(userid, results):
    """Computes the number of points which the ping results is worth, and
    sends a request to the leaderboards server to add that number to
    the leaderboards for the given userid."""
    # Compute how many points the given results are worth.
    points = len(results)

    # Send them to server.
    logger.debug('Adding %d to score of user "%s"', points, userid)
    zmq_incr_score_socket.send_json({'userid': userid,
                                     'score_increment': points})


def get_leaderboards():
    """Retrieves the latest leaderboards top scores."""
    top_scores = leaderboards.get_latest()
    logger.debug('Current leaderboards top score: %s', top_scores)
    return top_scores


def store_results(results):
    """Stores ping results. The results are sent via Zeromq to a
    storage_server instance."""
    logger.debug('Storing ping results: %s', results)
    zmq_send_results_socket.send_json(results)

if __name__ == "__main__":
    t0 = time.time()
    for i in range(10000):
        get_pings("132.204.25.179")
    t1 = time.time()
    print "Time per request (ms):", (t1 - t0) / 10000. * 1000
