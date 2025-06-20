#!/usr/bin/python3
#
# Scan fail2ban log and aggregate single banned IPv4 addresses into banned networks
#
# Source: https://github.com/WKnak/fail2ban-block-ip-range
#
# (P) & (C) 2021-2024 William Knak <williamknak@gmail.com>
# (P) & (C) 2024-2024 Peter Bieringer <pb@bieringer.de>

import argparse
import os
import re
import sys
from collections import defaultdict
from datetime import datetime
from ipaddress import IPv4Network

if sys.version_info < (3, 7, 0):
    from subprocess import PIPE, run
else:
    from subprocess import run

file_default = "/var/log/fail2ban.log"
maxage_default = "8h"
countlimit_default = 7

parser = argparse.ArgumentParser(
    prog="fail2ban-block-ip-range.py",
    description="Scan fail2ban log and aggregate single banned IPv4 addresses into banned networks",
    epilog="Defaults: FILE={} MAXAGE={} COUNTLIMIT={}".format(file_default, maxage_default, str(countlimit_default)),
)

parser.add_argument("-v", "--verbose"   , action="store_true")  # on/off flag
parser.add_argument("-q", "--quiet"     , action="store_true")  # on/off flag
parser.add_argument("-d", "--debug"     , action="store_true")  # on/off flag
parser.add_argument("-D", "--dryrun"    , action="store_true")  # on/off flag
parser.add_argument("-l", "--countlimit", action="store", type=int, default=countlimit_default)
parser.add_argument("-f", "--file"      , action="store", type=str, default=file_default)
parser.add_argument("-a", "--maxage"    , action="store", type=str, default=maxage_default)
parser.add_argument("-i", "--include_jail", action="append", type=str, default=[], help="Jail inclusions can be used multile times. Inclusions override the default 'all'.")
parser.add_argument("-x", "--exclude_jail", action="append", type=str, default=[], help="Jail exclusions can be used multile times. Excluding a jail that is also included is not supported.")

args = parser.parse_args()

fail2ban_log_file = args.file
max_age = args.maxage
countLimit = args.countlimit
includeJail = args.include_jail
excludeJail = args.exclude_jail

# convert max_age into seconds
age_pattern = re.compile("^([0-9]+)([smhdw])$")
m = age_pattern.match(max_age)
seconds_per_unit = {
    "s": 1,
    "m": 60,
    "h": 60 * 60,
    "d": 60 * 60 * 24,
    "w": 60 * 60 * 24 * 7,
}

if m:
    max_age_seconds = int(m.group(1)) * seconds_per_unit[m.group(2)]
    if args.debug:
        print("Filter entries older {} = {}s".format(max_age, max_age_seconds))
else:
    print("MAXAGE not valid: {}".format(max_age))
    exit(1)

dt_now = datetime.now()

if not os.path.isfile(fail2ban_log_file):
    print("File not found: {}".format(fail2ban_log_file))
    exit(1)

if args.debug:
    print("Logfile to analyze: {}".format(fail2ban_log_file))
    print("Count limit: {}".format(countLimit))

file = open(fail2ban_log_file, mode="r")

fail2ban_log_pattern = re.compile(r"^([0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}).* fail2ban.filter.*\[[0-9]+\]:.*\[([^]]+)\] Found ([0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3})")

if sys.version_info < (3, 7, 0):
    # fallback for Python < 3.7
    fail2ban_datetime_pattern = re.compile("^([0-9]{4})-([0-9]{2})-([0-9]{2}) ([0-9]{2}):([0-9]{2}):([0-9]{2})$")
    if args.debug:
        print("Fallback code for Python < 3.7 activated")

myjailip = defaultdict(lambda: defaultdict(int))
mylist = defaultdict(lambda: defaultdict(int))
finalList = defaultdict(lambda: defaultdict(int))


##### Functions
def printdict(var):
    for jail in var:
        print(" jail '{}'".format(jail))
        for ip in var[jail]:
            count = var[jail][ip]
            print("  {}: {}".format(ip, count))


# PART 1: filtering messages and IPs
#
# 1.1) this script searches for fail2ban.log for detections
# 1.2) then it extracts the IPs after filter log line by age
#
# PART 2: reads the IP list detected and iterate
#
# 2.1) iterate IPs and count

while True:
    line = file.readline()
    m = fail2ban_log_pattern.search(line)
    if m:
        timedate = m.group(1)
        if sys.version_info < (3, 7, 0):
            t = fail2ban_datetime_pattern.search(timedate)
            assert t is not None
            dt = datetime(int(t.group(1)), int(t.group(2)), int(t.group(3)), int(t.group(4)), int(t.group(5)), int(t.group(6)))
        else:
            # datetime.fromisoformat was added in Python 3.7
            dt = datetime.fromisoformat(timedate)

        jail = m.group(2)
        ip = m.group(3)
        dt_delta = int((dt_now - dt).total_seconds())
        if dt_delta > max_age_seconds:
            if args.debug:
                print("Found IPv4: {} {}s jail '{}' {} -> SKIP".format(timedate, dt_delta, jail, ip))
            continue

        if args.debug:
            print("Found IPv4: {} {}s jail '{}' {} -> JAIL-CHECK".format(timedate, dt_delta, jail, ip))

        if len(includeJail) > 0:
            if jail in includeJail:
                if args.debug:
                    print("Found IPv4: {} {}s jail '{}' included -> STORE".format(timedate, dt_delta, jail))
            else:
                if args.debug:
                    print("Found IPv4: {} {}s jail '{}' not included -> SKIP".format(timedate, dt_delta, jail))
                continue
        elif len(excludeJail) > 0:
            if jail in excludeJail:
                if args.debug:
                    print("Found IPv4: {} {}s jail '{}' excluded -> SKIP".format(timedate, dt_delta, jail))
                continue
            else:
                if args.debug:
                    print("Found IPv4: {} {}s jail '{}' not excluded -> STORE".format(timedate, dt_delta, jail))
        else:
            if args.debug:
                print("Found IPv4: {} {}s no jail in- or exclusions -> STORE".format(timedate, dt_delta))

        myjailip[jail][ip] += 1

        # 2.2) iterate from cidr/32 down to 23 (descending)
        for cidr in range(32, 23, -1):
            ipnet = IPv4Network(ip + "/" + str(cidr), False)
            index = str(ipnet.network_address) + "/" + str(cidr)

            # 2.3) add the network, jail and count of events into a dictionary
            mylist[jail][index] += 1

    if not line:
        break

file.close()

if args.debug:
    print("List per jail/ip:")
    printdict(myjailip)
    print("List per jail/index:")
    printdict(mylist)

#
# PART 3: iterate IPs again, and get the best choice network range
#
for jail in myjailip:
    for ip in myjailip[jail]:
        maxCount = 0
        nextIndex = False

        # 3.2 iterate CIDR (now in ascending order)
        for cidr in range(22, 33):
            ipnet = IPv4Network(ip + "/" + str(cidr), False)
            index = str(ipnet.network_address) + "/" + str(cidr)
            curCount = mylist[jail][index]
            if curCount >= maxCount:
                maxCount = curCount
                netIndex = index

            # 3.3 if count decreases, than we've already got our best range
            if curCount < maxCount:
                # found good network
                continue

        # 3.4 if netIndex is set and maxCount is above the limit, add range to list
        if netIndex:
            if not netIndex.endswith("/32"):
                if maxCount > countLimit:
                    finalList[jail][netIndex] = maxCount
                else:
                    if args.debug:
                        print("Skip IPv4: {} (count {} below limit {})".format(netIndex, maxCount, countLimit))
            else:
                if args.debug:
                    print("Skip IPv4: {} (not a network)".format(netIndex))

if args.debug:
    print("Final list of networks to block per jail:")
    printdict(finalList)

#
# PART 4: call fail2ban
#
fail2ban_command = "fail2ban-client set {} banip {}"
fail2ban_get = "fail2ban-client get {} banned {}"

for jail in finalList:
    for ip in finalList[jail]:
        getban_command = fail2ban_get.format(jail, ip)
        if sys.version_info < (3, 7, 0):
            # fallback for Python < 3.7
            banned = run(getban_command, stdout=PIPE, stderr=PIPE, universal_newlines=True, shell=True)
        else:
            banned = run(getban_command, capture_output=True, text=True, shell=True)

        if banned.returncode != 0:
            print("Unable to retrieve current status for jail '{}' {}: {}".format(jail, ip, banned.stderr))
            # continue ## old versions of fail2ban don't have get status for jail

        if banned.returncode != 0 or ((banned.returncode == 0) and (banned.stdout.strip() == "0")):
            banIP_command = fail2ban_command.format(jail, ip)
            if not args.dryrun:
                if sys.version_info < (3, 7, 0):
                    # fallback for Python < 3.7
                    result = run(banIP_command, stdout=PIPE, stderr=PIPE, universal_newlines=True, shell=True)
                else:
                    result = run(banIP_command, capture_output=True, text=True, shell=True)

                if result.returncode != 0:
                    print("Unable to ban for jail '{}' {}: {}".format(jail, ip, result.stderr))
                    continue

                if result.stdout.strip() == "1":
                    if not args.quiet:
                        print("jail '{}' successful ban aggregated IPv4 network: {}".format(jail, ip))
                else:
                    print("jail '{}' unsuccessful try to ban aggregated IPv4 network: {} (result: {})".format(jail, ip, result.stdout.strip()))
            else:
                print("jail '{}' would ban aggregated IPv4 network: {} (dry-run)".format(jail, ip))
        else:
            if args.verbose:
                print("jail '{}' aggregated IPv4 network already banned: {}".format(jail, ip))
