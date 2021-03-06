#!/usr/bin/env python
# -*- coding: UTF-8 -*-
#
# FGFW_Lite.py A Proxy Server help go around the Great Firewall
#
# Copyright (C) 2014 - 2015 Jiang Chao <sgzz.cj@gmail.com>
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the
# Free Software Foundation; either version 2 of the License, or (at your
# option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
# See the GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program; if not, see <http://www.gnu.org/licenses>.

from __future__ import print_function, division

import re
import time
import urlparse
from threading import Timer
from collections import defaultdict
from util import parse_hostport


class ExpiredError(Exception):
    def __init__(self, rule):
        self.rule = rule


class ap_rule(object):

    def __init__(self, rule, msg=None, expire=None):
        super(ap_rule, self).__init__()
        self.rule = rule.strip()
        if len(self.rule) < 3 or self.rule.startswith(('!', '[')) or '#' in self.rule or ' ' in self.rule:
            raise ValueError("invalid abp_rule: %s" % self.rule)
        self.msg = msg
        self.expire = expire
        self.override = self.rule.startswith('@@')
        self._regex = self._parse()

    def _parse(self):
        def parse(rule):
            if rule.startswith('||'):
                regex = rule.replace('.', r'\.').replace('?', r'\?').replace('/', '').replace('*', '[^/]*').replace('^', r'[\/:]').replace('||', '^(?:https?://)?(?:[^/]+\.)?') + r'(?:[:/]|$)'
                return re.compile(regex)
            elif rule.startswith('/') and rule.endswith('/'):
                return re.compile(rule[1:-1])
            elif rule.startswith('|https://'):
                i = rule.find('/', 9)
                regex = rule[9:] if i == -1 else rule[9:i]
                regex = r'^(?:https://)?%s(?:[:/])' % regex.replace('.', r'\.').replace('*', '[^/]*')
                return re.compile(regex)
            else:
                regex = rule.replace('.', r'\.').replace('?', r'\?').replace('*', '.*').replace('^', r'[\/:]')
                regex = re.sub(r'^\|', r'^', regex)
                regex = re.sub(r'\|$', r'$', regex)
                if not rule.startswith(('|', 'http://')):
                    regex = re.sub(r'^', r'^http://.*', regex)
                return re.compile(regex)

        return parse(self.rule[2:]) if self.override else parse(self.rule)

    def match(self, uri):
        if self.expire and self.expire < time.time():
            raise ExpiredError(self)
        return self._regex.search(uri)

    def __repr__(self):
        return '<ap_rule: %s>' % self.rule


class ap_filter(object):
    KEYLEN = 6

    def __init__(self, lst=None):
        self.excludes = []
        self.matches = []
        self.domains = set()
        self.domain_endswith = tuple()
        self.exclude_domains = set()
        self.exclude_domain_endswith = tuple()
        self.url_startswith = tuple()
        self.fast = defaultdict(list)
        self.rules = set()
        if lst:
            for rule in lst:
                self.add(rule)

    def add(self, rule, expire=None):
        rule = rule.strip()
        if len(rule) < 3 or rule.startswith(('!', '[')) or '#' in rule or '$' in rule:
            return
        if '||' in rule and '/' in rule[:-1]:
            return self.add(rule.replace('||', '|http://'))
        if rule.startswith('||') and '*' not in rule:
            self._add_domain(rule)
        elif rule.startswith('@@||') and '*' not in rule:
            self._add_exclude_domain(rule)
        elif rule.startswith(('|https://', '@', '/')):
            self._add_slow(rule)
        elif rule.startswith('|http://') and '*' not in rule:
            self._add_urlstartswith(rule)
        elif any(len(s) > (self.KEYLEN) for s in rule.split('*')):
            self._add_fast(rule)
        else:
            self._add_slow(rule)
        self.rules.add(rule)
        if expire:
            Timer(expire, self.remove, (rule, )).start()

    def _add_urlstartswith(self, rule):
        temp = set(self.url_startswith)
        temp.add(rule[1:])
        self.url_startswith = tuple(temp)

    def _add_fast(self, rule):
        lst = [s for s in rule.split('*') if len(s) > self.KEYLEN]
        o = ap_rule(rule)
        key = lst[-1][self.KEYLEN * -1:]
        self.fast[key].append(o)

    def _add_slow(self, rule):
        o = ap_rule(rule)
        lst = self.excludes if o.override else self.matches
        lst.append(o)

    def _add_exclude_domain(self, rule):
        rule = rule.rstrip('/^')
        self.exclude_domains.add(rule[4:])
        temp = set(self.exclude_domain_endswith)
        temp.add('.' + rule[4:])
        self.exclude_domain_endswith = tuple(temp)

    def _add_domain(self, rule):
        rule = rule.rstrip('/^')
        self.domains.add(rule[2:])
        temp = set(self.domain_endswith)
        temp.add('.' + rule[2:])
        self.domain_endswith = tuple(temp)

    def match(self, url, host=None, domain_only=False):
        if host is None:
            if '://' in url:
                host = urlparse.urlparse(url).hostname
            else:  # www.google.com:443
                host = parse_hostport(url)[0]
        if self._listmatch(self.excludes, url):
            return False
        if self._domainmatch(host) is not None:
            return self._domainmatch(host)
        if domain_only:
            return None
        if url.startswith(self.url_startswith):
            return True
        if self._fastmatch(url):
            return True
        if self._listmatch(self.matches, url):
            return True

    def _domainmatch(self, host):
        if host in self.exclude_domains:
            return False
        if host.endswith(self.exclude_domain_endswith):
            return False
        if host in self.domains:
            return True
        if host.endswith(self.domain_endswith):
            return True

    def _fastmatch(self, url):
        if url.startswith('http://'):
            i, j = 0, self.KEYLEN
            while j <= len(url):
                s = url[i:j]
                if s in self.fast:
                    if self._listmatch(self.fast[s], url):
                        return True
                i, j = i + 1, j + 1

    def _listmatch(self, lst, url):
        return any(r.match(url) for r in lst)

    def remove(self, rule):
        if rule in self.rules:
            if rule.startswith('||') and '*' not in rule:
                rule = rule.rstrip('/')
                self.domains.discard(rule[2:])
                temp = set(self.domain_endswith)
                temp.discard('.' + rule[2:])
                self.domain_endswith = tuple(temp)
            elif rule.startswith('@@||') and '*' not in rule:
                rule = rule.rstrip('/')
                self.exclude_domains.discard(rule[4:])
                temp = set(self.exclude_domain_endswith)
                temp.discard('.' + rule[4:])
                self.exclude_domain_endswith = tuple(temp)
            elif rule.startswith(('|https://', '@', '/')):
                lst = self.excludes if rule.startswith('@') else self.matches
                for o in lst[:]:
                    if o.rule == rule:
                        lst.remove(o)
                        break
            elif rule.startswith('|http://') and '*' not in rule:
                temp = set(self.url_startswith)
                temp.discard(rule[1:])
                self.url_startswith = tuple(temp)
            elif any(len(s) > (self.KEYLEN) for s in rule.split('*')):
                lst = [s for s in rule.split('*') if len(s) > self.KEYLEN]
                key = lst[-1][self.KEYLEN * -1:]
                for o in self.fast[key][:]:
                    if o.rule == rule:
                        self.fast[key].remove(o)
                        if not self.fast[key]:
                            del self.fast[key]
                        break
            else:
                lst = self.excludes if rule.startswith('@') else self.matches
                for o in lst[:]:
                    if o.rule == rule:
                        lst.remove(o)
                        break
            self.rules.discard(rule)

if __name__ == "__main__":
    gfwlist = ap_filter()
    lst = ['inxian.com',
           '||twitter.com',
           '@@||qq.com',
           '|https://doc*.google.com',
           '@@|http://www.163.com',
           '|http://zh.wikipedia.com']
    for rule in lst:
        gfwlist.add(rule)

    def show():
        print(gfwlist.excludes)
        print(gfwlist.matches)
        print(gfwlist.domains)
        print(gfwlist.domain_endswith)
        print(gfwlist.exclude_domains)
        print(gfwlist.exclude_domain_endswith)
        print(gfwlist.url_startswith)
        print(gfwlist.fast)
    show()
    for r in list(gfwlist.rules):
        print('remove %s' % r)
        gfwlist.remove(r)
        show()

    gfwlist = ap_filter()
    t = time.time()
    with open('gfwlist.txt') as f:
        data = f.read()
        if '!' not in data:
            import base64
            data = ''.join(data.split())
            data = base64.b64decode(data).decode()
            for line in data.splitlines():
                # if line.startswith('||'):
                gfwlist.add(line)
            del data
    print('loading: %fs' % (time.time() - t))
    print('result for inxian: %r' % gfwlist.match('http://www.inxian.com', 'www.inxian.com'))
    print('result for twitter: %r' % gfwlist.match('www.twitter.com:443', 'www.twitter.com'))
    print('result for 163: %r' % gfwlist.match('http://www.163.com', 'www.163.com'))
    print('result for alipay: %r' % gfwlist.match('www.alipay.com:443', 'www.alipay.com'))
    print('result for qq: %r' % gfwlist.match('http://www.qq.com', 'www.qq.com'))
    print('result for keyword: %r' % gfwlist.match('http://www.test.com/iredmail.org', 'www.test.com'))
    print('result for url_startswith: %r' % gfwlist.match('http://itweet.net/whatever', 'itweet.net'))
    import sys
    url = sys.argv[1] if len(sys.argv) > 1 else 'http://www.163.com'
    host = urlparse.urlparse(url).hostname
    print('%s, %s' % (url, host))
    print(gfwlist.match(url, host))
    t = time.time()
    for _ in range(10000):
        gfwlist.match(url, host)
    print('KEYLEN = %d' % gfwlist.KEYLEN)
    print('10000 query for %s, %fs' % (url, time.time() - t))
    print('O(1): %d' % (len(gfwlist.domains) + len(gfwlist.exclude_domains)))
    print('O(n): %d' % (len(gfwlist.excludes) + len(gfwlist.matches)))
    l = gfwlist.fast.keys()
    l = sorted(l, key=lambda x: len(gfwlist.fast[x]))
    for i in l[-20:]:
        print('%r : %d' % (i, len(gfwlist.fast[i])))
