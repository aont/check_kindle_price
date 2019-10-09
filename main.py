#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import io
import os
import sys
import codecs
import time
import datetime
import re
import traceback
import json

import requests
import psycopg2
import psycopg2.extras
import requests
import lxml.html
import cssselect
import sendgrid

sleep_duration = 5
AMAZON_CO_JP='https://www.amazon.co.jp/'
amazon_headers = {
    'authority': 'www.amazon.co.jp',
    'upgrade-insecure-requests': '1',
    'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/73.0.3683.103 Safari/537.36',
    'dnt': '1',
    'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3',
    'accept-encoding': 'gzip, deflate, br',
    'accept-language': 'ja-JP,ja;q=0.9,en-US;q=0.8,en;q=0.7',
    'referer': AMAZON_CO_JP,
}

def send_mail(message_str, subject):
    sys.stderr.write("[info] mailing via sendgrid\n")
    sg_username = os.environ["SENDGRID_USERNAME"]
    sg_recipient = os.environ["SENDGRID_RECIPIENT"]
    sg_apikey = os.environ["SENDGRID_APIKEY"]
    sg_client = sendgrid.SendGridAPIClient(sg_apikey)
    sg_from = sendgrid.Email(name="Check Kindle Price", email=sg_username)
    message = sendgrid.Mail(from_email=sg_from, to_emails=[sg_recipient], subject=subject, html_content=message_str)
    message.reply_to = sg_recipient
    sg_client.send(message)

def str_abbreviate(str_in):
    len_str_in = len(str_in)
    if len_str_in > 128*2+10:
        return str_in[0:128] + " ... " + str_in[-128:]
    else:
        return str_in

def pg_execute(pg_cur, query, param=None):
    param_str = str_abbreviate("%s" % param)
    sys.stderr.write('[info] postgres: %s param=%s\n' % (query, param_str))
    return pg_cur.execute(query, param)

def pg_init_json(pg_cur, table_name, key_name):
    pg_result = pg_execute(pg_cur, "select 1 from pg_tables where schemaname='public' and tablename=%s ;", [table_name])
    pg_result = pg_cur.fetchone()
    if pg_result is None:
        #sys.stderr.write('[info] creating table\n')
        pg_execute(pg_cur, "create table %s (key text unique, value text);" % (table_name))
    elif 1 != pg_result[0] :
        raise Exception("exception")

    pg_execute(pg_cur, 'select value from %s where key=%%s;' % table_name, [key_name])
    pg_result = pg_cur.fetchone()
    
    if pg_result is None:
        pg_execute(pg_cur, 'insert into %s VALUES (%%s, %%s);' % table_name, [key_name, "{}"])
        pg_data = {}
    else:
        sys.stderr.write('[info] data=%s\n' % str_abbreviate(pg_result[0]))
        pg_data = json.loads(pg_result[0])
    return pg_data

def pg_update_json(pg_cur, table_name, key_name, pg_data):
    return pg_execute(pg_cur, 'update %s set value = %%s where key = %%s;' % table_name, [json.dumps(pg_data, ensure_ascii=False), key_name])

def get_wish_list(sess, list_id):
    item_ary = []
    lastEvaluatedKey = None
    while True:
        lastEvaluatedKey = get_wish_list_page(sess, list_id, item_ary, lastEvaluatedKey)
        if lastEvaluatedKey is None:
            break
        else:
            sys.stderr.write("[info] lastEvaluatedKey: %s\n" % lastEvaluatedKey)
        
    sys.stderr.write("[info] get_wish_list done\n")
    return item_ary

def get_wish_list_page(sess, list_id, item_ary, lastEvaluatedKey = None):

    url = AMAZON_CO_JP + 'hz/wishlist/ls/' + list_id
    if lastEvaluatedKey is not None:
        url += "?lek=" + lastEvaluatedKey

    try_num = 0
    max_try = 5
    while True:
        result = sess.get(url, headers = amazon_headers)
        if requests.codes.get("ok") == result.status_code:
            break
        else:
            sys.stderr.write("[info] amazon status_code = %s\n" % result.status_code)
            sys.stderr.write("[info] wait for 5s and retry\n")
            # sys.stderr.flush()
            try_num += 1
            if try_num == max_try:
                send_mail(result.text, "Alert")
                raise Exception('unexpected')
            time.sleep(sleep_duration)
            continue
    
    product_lxml = lxml.html.fromstring(result.text)
    try:
        g_items = product_lxml.get_element_by_id('g-items')
    except KeyError as e:
        send_mail(result.text, "Alert")
        raise e
    li_ary = g_items.cssselect('li')

    # item_ary = []
    dp_pattern = re.compile('/dp/(.*?)/')
    for li in li_ary:
        data_itemid = li.get("data-itemid")
        # sys.stderr.write("[Info] data-itemid: %s \n" % data_itemid)
        itemname_elem = li.get_element_by_id('itemName_%s' % data_itemid)
        item_title = itemname_elem.get('title')
        item_href = itemname_elem.get('href')
        # item_html = itemname_elem.text
        # sys.stdout.write("%s %s %s\n" % (item_title, item_href, item_html))
        dp_match = dp_pattern.search(item_href)
        if dp_match is None:
            raise Exception("unexpected")
        # sys.stdout.write("dpid %s\n" % dp_match.group(1))

        dp_id = dp_match.group(1)
        item_ary.append({"dp": dp_id, 'title': item_title})
    
    # showMoreUrl = product_lxml.cssselect('#g-items > form > input.showMoreUrl')
    lastEvaluatedKey_elems = product_lxml.cssselect('input.lastEvaluatedKey')
    if len(lastEvaluatedKey_elems)==0:
        return None
    elif len(lastEvaluatedKey_elems)==1:
        return lastEvaluatedKey_elems[0].get("value")
    else:
        raise Exception("unexpected")
    # return item_ary


AMAZON_DP= AMAZON_CO_JP  + 'dp/'
def check_amazon(sess, dp):
    sys.stderr.write('[info] check_amazon dp=%s\n' % dp)
    product_uri = AMAZON_DP + dp

    try_num = 0
    max_try = 5
    while True:
        
        result = sess.get(product_uri, headers = amazon_headers)
        # result = sess.get(product_uri)
        if requests.codes.get("unavailable") == result.status_code:
            sys.stderr.write("[info] amazon temporarily unavailable\n")
            sys.stderr.write("[info] wait for 5s and retry\n")
            # sys.stderr.flush()
            time.sleep(sleep_duration)
            try_num += 1
            if try_num == max_try:
                send_mail(result.text, "Alert")
                # sys.stdout.write(result.text)
                raise Exception('unexpected')
            else:
                continue

        if requests.codes.get("ok") != result.status_code:
            sys.stderr.write("[info] amazon status_code = %s\n" % result.status_code)
            sys.stderr.write("[info] wait for 5s and retry\n")
            # sys.stderr.flush()
            time.sleep(sleep_duration)
            try_num += 1
            if try_num == max_try:
                send_mail(result.text, "Alert")
                # sys.stdout.write(result.text)
                raise Exception('unexpected')
            else:
                continue
        
        product_lxml = lxml.html.fromstring(result.text)
        price_td_ary = product_lxml.cssselect('tr.kindle-price> td.a-color-price')

        if len(price_td_ary) != 1:
            sys.stderr.write("[warn] amazon html format error. retrying...\n")
            time.sleep(sleep_duration)
            # sys.stderr.flush()
            # sys.stdout.write(result.content)
            # codecs.getwriter('utf_8')(sys.stdout).write(result.text)
            # print result.text
            try_num += 1
            if try_num == max_try:
                send_mail(result.text, "Alert")
                # sys.stdout.write(result.text)
                raise Exception('unexpected')
            else:
                continue
        else:
            break
        
    price_td = price_td_ary[0]
    price_innerhtml = lxml.etree.tostring(price_td).decode()
    # sys.stderr.write('[info] price_innerhtml=%s\n' % price_innerhtml)
    # print price_innerhtml
    price_pattern = re.compile('&#65509;\s*([0-9,]+)')
    price_match_obj = price_pattern.search(price_innerhtml)
    if price_match_obj is not None:
        price_num = int(price_match_obj.group(1).replace(',',''))
        # print "%s yen" % price_num

    point_num = 0
    point_td_ary = product_lxml.cssselect('tr.loyalty-points > td.a-align-bottom')
    if len(point_td_ary) > 1:
        raise Exception("unexpected")
    elif len(point_td_ary) == 1:
        point_td = point_td_ary[0]
        point_innerhtml = lxml.etree.tostring(point_td).decode()
        # print point_innerhtml
        point_pattern = re.compile('([0-9,]+)pt')
        point_match_obj = point_pattern.search(point_innerhtml)
        if point_match_obj is not None:
            point_num = int(point_match_obj.group(1).replace(',',''))

    try:
        # upsell_button_announce = 
        product_lxml.get_element_by_id('upsell-button-announce')
        # if upsell_button_announce is not None:
        sys.stderr.write("[Info] unlimited!\n")
        price_num = - price_num
        point_num = - point_num
    except KeyError:
        pass

    return (price_num, point_num)

def main():
    amazon_sess = requests.session()    

    pg_url = os.environ['DATABASE_URL']
    table_name = 'generic_text_data'
    key_name = 'kindle_price'
    pg_conn = psycopg2.connect(pg_url)
    pg_cur = pg_conn.cursor()
    kindle_price_data = pg_init_json(pg_cur, table_name, key_name)

    list_id = os.environ['AMAZON_WISH_LIST_ID']
    item_ary = get_wish_list(amazon_sess, list_id)

    messages = []
    kindle_price_data_new = {}
    for item in item_ary:
        dp = item['dp']
        item_title = item['title']

        if dp not in kindle_price_data:
            prev_net_price = -1
        else:
            prev_net_price = kindle_price_data[dp]["price"] - kindle_price_data[dp]["point"]

        datetime_now = datetime.datetime.now()
        new_state = check_amazon(amazon_sess, dp)
        new_net_price = new_state[0] - new_state[1]
        sys.stderr.write('[info] price=%s point=%s net_price=%s\n' % (new_state[0], new_state[1], new_net_price))
        if new_net_price != prev_net_price:
            mes = "<a href=\"%s%s\">%s</a> %s <- %s" % (AMAZON_DP, dp, item_title, new_net_price, prev_net_price)
            messages.append(mes)
            sys.stderr.write("[info] %s\n" %mes)
        
        kindle_price_data_new[dp] = { \
            "title": item_title, \
            "price": new_state[0], \
            "point": new_state[1], \
            "date": datetime_now.strftime("%Y/%m/%d %H:%M:%S") \
        }

    pg_update_json(pg_cur, table_name, key_name, kindle_price_data_new)

    if len(messages)>0:
        send_mail("<br />\n".join(messages), "Update of Kindle Price")
    
    pg_cur.close()
    pg_conn.commit()
    pg_conn.close()

if __name__ == '__main__':
    main()
