#!/usr/bin/env python2
# -*- coding: utf-8 -*-

import io
import os
import sys
import codecs
import time
import datetime
import re

import requests
import psycopg2
import psycopg2.extras
import requests
import lxml.html
import cssselect


class LINE(object):
    def __init__(self, sess, line_notify_token):
        self.sess = sess
        self.line_notify_token = line_notify_token
        self.line_notify_api = 'https://notify-api.line.me/api/notify'
        self.headers = {'Authorization': 'Bearer ' + line_notify_token}

    def notify(self, message):
        for t in xrange(5):
            try:
                line_notify = self.sess.post(self.line_notify_api, data = {'message': message}, headers = self.headers)
                break
            except requests.exceptions.ConnectionError as e:
                sys.stderr.write(e.message)
                continue

AMAZON='https://www.amazon.co.jp/dp/'

def check_amazon(sess, dp):
    headers = {
        'authority': 'www.amazon.co.jp',
        'upgrade-insecure-requests': '1',
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/73.0.3683.103 Safari/537.36',
        'dnt': '1',
        'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3',
        'accept-encoding': 'gzip, deflate, br',
        'accept-language': 'ja-JP,ja;q=0.9,en-US;q=0.8,en;q=0.7',
    }


    product_uri = AMAZON + dp

    result = sess.get(product_uri, headers = headers)
    product_lxml = lxml.html.fromstring(result.text)

    price_td_ary = product_lxml.cssselect('#buybox > div > table > tr.kindle-price> td.a-color-price.a-size-medium.a-align-bottom')
    if len(price_td_ary) != 1:
        codecs.getwriter('utf_8')(sys.stdout).write(result.text)
        # print result.text
        raise
    price_td = price_td_ary[0]
    price_innerhtml = lxml.etree.tostring(price_td)
    # print price_innerhtml
    price_pattern = re.compile(r'&#65509; ([0-9,]+)')
    price_match_obj = price_pattern.search(price_innerhtml)
    if price_match_obj is not None:
        price_num = int(price_match_obj.group(1).replace(',',''))
        # print "%s yen" % price_num

    point_num = 0
    point_td_ary = product_lxml.cssselect('#buybox > div > table > tr.loyalty-points > td.a-align-bottom')
    if len(point_td_ary) > 1:
        raise
    elif len(point_td_ary) == 1:
        point_td = point_td_ary[0]
        point_innerhtml = lxml.etree.tostring(point_td)
        # print point_innerhtml
        point_pattern = re.compile(r'([0-9,]+)pt')
        point_match_obj = point_pattern.search(point_innerhtml)
        if point_match_obj is not None:
            point_num = int(point_match_obj.group(1).replace(',',''))
            # print "%s pt" % point_num
    return (price_num, point_num)
            
        
if __name__ == '__main__':
    sess = requests.session()
    
    
    pg_url = os.environ['DATABASE_URL']
    pg_conn = psycopg2.connect(pg_url)
    # pg_cur = pg_conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    pg_cur = pg_conn.cursor()

    line_notify_token = os.environ['LINE_TOKEN']
    line = LINE(sess, line_notify_token)

    dp_ary = ['B0111OGVTM', 'B00DLT0B9M']
    for dp in dp_ary:
        
        pg_cur.execute('select price - point from amazon_price where dp=%s order by date desc;', [dp])
        pg_result = pg_cur.fetchone()
        if pg_result is None:
            prev_net_price = -1
        else:
            prev_net_price = pg_result[0]

        datetime_now = datetime.datetime.now()
        # print "datetime: %s" % datetime_now
        new_state = check_amazon(sess, dp)
        new_net_price = new_state[0] - new_state[1]

        if new_net_price != prev_net_price:
            line.notify("%s%s %s <- %s" % (AMAZON, dp, new_net_price, prev_net_price))

        # if new_net_price != prev_net_price:
        #     print("%s%s %s <- %s (%s)" % (AMAZON, dp, new_net_price, prev_net_price, datetime_now))
        
        pg_cur.execute('insert into amazon_price VALUES (%s, %s, %s, %s);', [dp, new_state[0], new_state[1], datetime_now])
        # pg_cur.execute('update amazon_price set price = 1555, point = 0, date = %s where dp = %s;', [datetime.datetime.now(), dp])
        
        
        # for row in pg_cur:
        #     print row

        # time.sleep(10)
        
    pg_conn.commit()
    
    pg_cur.close()
    pg_conn.close()

    
    
