#!/usr/bin/env python2
# -*- coding: utf-8 -*-

import io
import os
import sys
import codecs
import time
import datetime
import re
import traceback

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
        # print message
        # return
        for t in xrange(5):
            try:
                line_notify = self.sess.post(self.line_notify_api, data = {'message': message}, headers = self.headers)
                if requests.codes.ok != line_notify.status_code:
                    sys.stderr.write("[info] line status_code = %s\n" % line_notify.status_code)
                    sys.stderr.write("[info] wait for 5s and retry\n")
                    # sys.stderr.flush()
                    time.sleep(5)
                    continue

                break
            except requests.exceptions.ConnectionError as e:
                sys.stderr.write("[warn] LINE ConnectionError occured. retrying...\n")
                sys.stderr.write(traceback.format_exc())
                # sys.stderr.flush()
                continue


AMAZON_CO_JP='https://www.amazon.co.jp/'
amazon_headers = {
    'authority': 'www.amazon.co.jp',
    'upgrade-insecure-requests': '1',
    'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/73.0.3683.103 Safari/537.36',
    'dnt': '1',
    'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3',
    'accept-encoding': 'gzip, deflate, br',
    'accept-language': 'ja-JP,ja;q=0.9,en-US;q=0.8,en;q=0.7',
}

def get_wish_list(sess, list_id):

    try_num = 0
    max_try = 5
    while True:
        result = sess.get(AMAZON_CO_JP + 'hz/wishlist/ls/' + list_id, headers = amazon_headers)
        if requests.codes.ok == result.status_code:
            break
        else:
            sys.stderr.write("[info] amazon status_code = %s\n" % result.status_code)
            sys.stderr.write("[info] wait for 5s and retry\n")
            # sys.stderr.flush()
            try_num += 1
            if try_num == max_try:
                raise
            time.sleep(5)
            continue
    
    product_lxml = lxml.html.fromstring(result.text)

    li_ary = product_lxml.cssselect('#g-items > li')
    if len(li_ary) == 0:
        raise

    dp_ary = []
    dp_pattern = re.compile(r'^/dp/([^d]+)/')
    for li in li_ary:
        data_itemid = li.get("data-itemid")
        # sys.stderr.write("[Info] data-itemid: %s \n" % data_itemid)
        itemname_elem = li.get_element_by_id('itemName_%s' % data_itemid)
        # item_title = itemname_elem.get('title')
        item_href = itemname_elem.get('href')
        # item_html = itemname_elem.text
        # sys.stdout.write("%s %s %s\n" % (item_title, item_href, item_html))
        dp_match = dp_pattern.match(item_href)
        if dp_match is None:
            raise
        # sys.stdout.write("dpid %s\n" % dp_match.group(1))

        dp_id = dp_match.group(1)
        dp_ary.append(dp_id)
    
    return dp_ary


AMAZON_DP= AMAZON_CO_JP  + 'dp/'
def check_amazon(sess, dp):

    product_uri = AMAZON_DP + dp

    try_num = 0
    max_try = 5
    while True:
        
        result = sess.get(product_uri, headers = amazon_headers)
        # result = sess.get(product_uri)
        if requests.codes.unavailable == result.status_code:
            sys.stderr.write("[info] amazon temporarily unavailable\n")
            sys.stderr.write("[info] wait for 5s and retry\n")
            # sys.stderr.flush()
            time.sleep(5)
            continue

        if requests.codes.ok != result.status_code:
            sys.stderr.write("[info] amazon status_code = %s\n" % result.status_code)
            sys.stderr.write("[info] wait for 5s and retry\n")
            # sys.stderr.flush()
            time.sleep(5)
            continue
        
        
        
        product_lxml = lxml.html.fromstring(result.text)
        price_td_ary = product_lxml.cssselect('#buybox > div > table > tr.kindle-price> td.a-color-price.a-size-medium.a-align-bottom')


        if len(price_td_ary) != 1:
            sys.stderr.write("[warn] amazon html format error. retrying...\n")
            # sys.stderr.flush()
            # sys.stdout.write(result.content)
            # codecs.getwriter('utf_8')(sys.stdout).write(result.text)
            # print result.text
            try_num += 1
            if try_num == max_try:
                raise
            else:
                continue
        else:
            break
        
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
    line_sess = requests.session()
    amazon_sess = requests.session()
    
    pg_url = os.environ['DATABASE_URL']
    table_name = 'amazon_price'
    pg_conn = psycopg2.connect(pg_url)
    pg_cur = pg_conn.cursor()

    line_notify_token = os.environ['LINE_TOKEN']
    line = LINE(line_sess, line_notify_token)


    pg_result = pg_cur.execute("select 1 from pg_tables where schemaname='public' and tablename=%s ;", [table_name])
    pg_result = pg_cur.fetchone()
    if pg_result is None:
        pg_cur.execute("create table %s (dp text, price integer, point integer, date timestamp);" % (table_name))
    elif 1 != pg_result[0] :
        raise
        
    # dp_ary = os.environ['AMAZON_GP_ARRAY'].split(',')
    list_id = os.environ['AMAZON_WISH_LIST_ID']
    dp_ary = get_wish_list(amazon_sess, list_id)
    for dp in dp_ary:
        
        pg_cur.execute('select price - point from %s where dp=%%s order by date desc;' % table_name, [dp])
        pg_result = pg_cur.fetchone()
        if pg_result is None:
            prev_net_price = -1
        else:
            prev_net_price = pg_result[0]

        datetime_now = datetime.datetime.now()
        new_state = check_amazon(amazon_sess, dp)
        new_net_price = new_state[0] - new_state[1]

        if new_net_price != prev_net_price:
            line.notify("%s%s %s <- %s (%s)" % (AMAZON_DP, dp, new_net_price, prev_net_price, datetime_now.strftime("%Y/%m/%d %H:%M:%S")))

        pg_cur.execute('insert into %s VALUES (%%s, %%s, %%s, %%s);' % table_name, [dp, new_state[0], new_state[1], datetime_now])
        
    pg_conn.commit()
    
    pg_cur.close()
    pg_conn.close()

    
    
