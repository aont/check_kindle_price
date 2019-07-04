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
        self.line_notify_api = u'https://notify-api.line.me/api/notify'
        self.headers = {u'Authorization': u'Bearer ' + line_notify_token}

    def notify(self, message):
        # print message
        # return
        for t in xrange(5):
            try:
                line_notify = self.sess.post(self.line_notify_api, data = {u'message': message}, headers = self.headers)
                if requests.codes.ok != line_notify.status_code:
                    sys.stderr.write(u"[info] line status_code = %s\n" % line_notify.status_code)
                    sys.stderr.write(u"[info] wait for 5s and retry\n")
                    # sys.stderr.flush()
                    time.sleep(5)
                    continue

                break
            except requests.exceptions.ConnectionError as e:
                sys.stderr.write(u"[warn] LINE ConnectionError occured. retrying...\n")
                sys.stderr.write(traceback.format_exc())
                # sys.stderr.flush()
                continue


AMAZON_CO_JP=u'https://www.amazon.co.jp/'
amazon_headers = {
    u'authority': u'www.amazon.co.jp',
    u'upgrade-insecure-requests': u'1',
    u'user-agent': u'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/73.0.3683.103 Safari/537.36',
    u'dnt': u'1',
    u'accept': u'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3',
    u'accept-encoding': u'gzip, deflate, br',
    u'accept-language': u'ja-JP,ja;q=0.9,en-US;q=0.8,en;q=0.7',
}

def get_wish_list(sess, list_id):

    try_num = 0
    max_try = 5
    while True:
        result = sess.get(AMAZON_CO_JP + u'hz/wishlist/ls/' + list_id, headers = amazon_headers)
        if requests.codes.ok == result.status_code:
            break
        else:
            sys.stderr.write(u"[info] amazon status_code = %s\n" % result.status_code)
            sys.stderr.write(u"[info] wait for 5s and retry\n")
            # sys.stderr.flush()
            try_num += 1
            if try_num == max_try:
                raise
            time.sleep(5)
            continue
    
    product_lxml = lxml.html.fromstring(result.text)

    li_ary = product_lxml.cssselect(u'#g-items > li')
    if len(li_ary) == 0:
        raise

    item_ary = []
    dp_pattern = re.compile(r'^/dp/([^d]+)/')
    for li in li_ary:
        data_itemid = li.get(u"data-itemid")
        # sys.stderr.write("[Info] data-itemid: %s \n" % data_itemid)
        itemname_elem = li.get_element_by_id(u'itemName_%s' % data_itemid)
        item_title = itemname_elem.get(u'title')
        item_href = itemname_elem.get(u'href')
        # item_html = itemname_elem.text
        # sys.stdout.write("%s %s %s\n" % (item_title, item_href, item_html))
        dp_match = dp_pattern.match(item_href)
        if dp_match is None:
            raise
        # sys.stdout.write("dpid %s\n" % dp_match.group(1))

        dp_id = dp_match.group(1)
        item_ary.append({u"dp": dp_id, u'title': item_title})
    
    return item_ary


AMAZON_DP= AMAZON_CO_JP  + u'dp/'
def check_amazon(sess, dp):

    product_uri = AMAZON_DP + dp

    try_num = 0
    max_try = 5
    while True:
        
        result = sess.get(product_uri, headers = amazon_headers)
        # result = sess.get(product_uri)
        if requests.codes.unavailable == result.status_code:
            sys.stderr.write(u"[info] amazon temporarily unavailable\n")
            sys.stderr.write(u"[info] wait for 5s and retry\n")
            # sys.stderr.flush()
            time.sleep(5)
            continue

        if requests.codes.ok != result.status_code:
            sys.stderr.write(u"[info] amazon status_code = %s\n" % result.status_code)
            sys.stderr.write(u"[info] wait for 5s and retry\n")
            # sys.stderr.flush()
            time.sleep(5)
            continue
        
        product_lxml = lxml.html.fromstring(result.text)
        price_td_ary = product_lxml.cssselect(u'div > table > tr.kindle-price> td.a-color-price.a-size-medium.a-align-bottom')

        if len(price_td_ary) != 1:
            sys.stderr.write(u"[warn] amazon html format error. retrying...\n")
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
    point_td_ary = product_lxml.cssselect(u'#buybox > div > table > tr.loyalty-points > td.a-align-bottom')
    if len(point_td_ary) > 1:
        raise
    elif len(point_td_ary) == 1:
        point_td = point_td_ary[0]
        point_innerhtml = lxml.etree.tostring(point_td)
        # print point_innerhtml
        point_pattern = re.compile(r'([0-9,]+)pt')
        point_match_obj = point_pattern.search(point_innerhtml)
        if point_match_obj is not None:
            point_num = int(point_match_obj.group(1).replace(u',',u''))
            # print "%s pt" % point_num

    try:
        upsell_button_announce = product_lxml.get_element_by_id(u'upsell-button-announce')
        # if upsell_button_announce is not None:
        sys.stderr.write("[Info] unlimited!\n")
        price_num = - price_num
        point_num = - point_num
    except KeyError:
        pass

    return (price_num, point_num)

def main():
    line_sess = requests.session()
    amazon_sess = requests.session()
    
    pg_url = os.environ[u'DATABASE_URL']
    table_name = u'amazon_price'
    pg_conn = psycopg2.connect(pg_url)
    pg_cur = pg_conn.cursor()

    line_notify_token = os.environ[u'LINE_TOKEN']
    line = LINE(line_sess, line_notify_token)


    pg_result = pg_cur.execute(u"select 1 from pg_tables where schemaname='public' and tablename=%s ;", [table_name])
    pg_result = pg_cur.fetchone()
    if pg_result is None:
        pg_cur.execute(u"create table %s (dp text, price integer, point integer, date timestamp);" % (table_name))
    elif 1 != pg_result[0] :
        raise
        
    # dp_ary = os.environ['AMAZON_GP_ARRAY'].split(',')
    list_id = os.environ[u'AMAZON_WISH_LIST_ID']
    item_ary = get_wish_list(amazon_sess, list_id)
    for item in item_ary:
        dp = item[u'dp']
        item_title = item[u'title']

        pg_cur.execute(u'select price - point from %s where dp=%%s order by date desc;' % table_name, [dp])
        pg_result = pg_cur.fetchone()
        if pg_result is None:
            prev_net_price = -1
        else:
            prev_net_price = pg_result[0]

            pg_cur.execute(u'delete from %s where dp=%%s;' % table_name, [dp])

        datetime_now = datetime.datetime.now()
        new_state = check_amazon(amazon_sess, dp)
        new_net_price = new_state[0] - new_state[1]

        if new_net_price != prev_net_price:
            line.notify(u"%s %s%s %s <- %s (%s)" % (item_title, AMAZON_DP, dp, new_net_price, prev_net_price, datetime_now.strftime(u"%Y/%m/%d %H:%M:%S")))


        pg_cur.execute(u'insert into %s VALUES (%%s, %%s, %%s, %%s);' % table_name, [dp, new_state[0], new_state[1], datetime_now])

        
    pg_conn.commit()
    
    pg_cur.close()
    pg_conn.close()


def amazon_test():
    amazon_sess = requests.session()
    # dp = u"B0192CTNQI"
    dp = u'B017NIF84E'
    new_state = check_amazon(amazon_sess, dp)
    sys.stdout.write("%s %s\n" % (new_state[0], new_state[1]) )

if __name__ == u'__main__':
    amazon_test()
    # main()