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
import base64
import inspect
import urllib.parse
import signal

import requests
import psycopg2
import psycopg2.extras
import requests
import lxml.html
import cssselect
import sendgrid
import sendgrid.helpers

sleep_duration = 5
max_try = 1

AMAZON_CO_JP='https://www.amazon.co.jp/'
amazon_headers = {
    'authority': 'www.amazon.co.jp',
    'upgrade-insecure-requests': '1',
    'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/73.0.3683.103 Safari/537.36',
    'dnt': '1',
    'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3',
    'accept-encoding': 'identity',
    'accept-language': 'ja-JP,ja;q=0.9,en-US;q=0.8,en;q=0.7',
    'referer': AMAZON_CO_JP,
}

def send_alert_mail(frame, attach_html):
    cf = frame.f_back
    send_mail("Exception: %s:%s:%s" % (cf.f_code.co_filename, cf.f_code.co_name, cf.f_lineno), "Check Kindle Price: Alert",  attach_html=attach_html)

def send_mail(message_str, subject, attach_html=None):
    sys.stderr.write("[info] mailing via sendgrid\n")
    sg_username = os.environ["SENDGRID_USERNAME"]
    sg_recipient = os.environ["SENDGRID_RECIPIENT"]
    sg_apikey = os.environ["SENDGRID_APIKEY"]
    sg_client = sendgrid.SendGridAPIClient(sg_apikey)
    
    sg_from = sendgrid.Email(name="Check Kindle Price", email=sg_username)
    message = sendgrid.Mail(from_email=sg_from, to_emails=[sg_recipient], subject=subject, html_content=message_str)
    message.reply_to = sg_recipient
    if attach_html:
        attachment_file = sendgrid.Attachment(file_content=base64.b64encode(attach_html).decode(), file_type="text/html", file_name="attach.html")
        message.add_attachment(attachment_file)
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
    # item_ary = []
    # lastEvaluatedKey = None
    lastEvaluatedKey_ref = [None]
    while True:
        for items in get_wish_list_page(sess, list_id, lastEvaluatedKey_ref):
            yield items
        lastEvaluatedKey = lastEvaluatedKey_ref[0] # get_wish_list_page(sess, list_id, item_ary, lastEvaluatedKey)
        if lastEvaluatedKey is None:
            break
        else:
            sys.stderr.write("[info] lastEvaluatedKey: %s\n" % lastEvaluatedKey)
        
    sys.stderr.write("[info] get_wish_list done\n")
    # return item_ary

AMAZON_LIST=urllib.parse.urljoin(AMAZON_CO_JP, '/hz/wishlist/ls/')
def get_wish_list_page(sess, list_id, lastEvaluatedKey_ref):

    lastEvaluatedKey = lastEvaluatedKey_ref[0]
    url = urllib.parse.urljoin(AMAZON_LIST, list_id)
    if lastEvaluatedKey:
        url += "?lek=" + lastEvaluatedKey

    try_num = 0
    while True:
        try:
            result = sess.get(url, headers = amazon_headers)
            amazon_headers["referer"] = url
            result.raise_for_status()

            product_lxml = lxml.html.fromstring(result.text)
            g_items = product_lxml.get_element_by_id('g-items')
            li_ary = g_items.cssselect('li')

            lastEvaluatedKey_elems = product_lxml.cssselect('input.lastEvaluatedKey')
            if len(lastEvaluatedKey_elems)==0:
                lastEvaluatedKey_ref[0] = None
            elif len(lastEvaluatedKey_elems)==1:
                lastEvaluatedKey_ref[0] = lastEvaluatedKey_elems[0].get("value")
            else:
                raise Exception("unexpected")
            
            dp_pattern = re.compile('/dp/(.*?)/')
            for li in li_ary:
                data_itemid = li.get("data-itemid")
                itemname_elem = li.get_element_by_id('itemName_%s' % data_itemid)
                item_title = itemname_elem.get('title')
                item_href = itemname_elem.get('href')
                dp_match = dp_pattern.search(item_href)
                if dp_match is None:
                    raise Exception("unexpected")

                dp_id = dp_match.group(1)
                yield {"dp": dp_id, 'title': item_title}

            break
            
        except Exception as e:
            try_num += 1
            if try_num == max_try:
                send_alert_mail(inspect.currentframe(), attach_html=result.content)
                raise e
            sys.stderr.write("[info] retry access in %ss\n" % sleep_duration)
            time.sleep(sleep_duration)
            continue

AMAZON_DP= urllib.parse.urljoin(AMAZON_CO_JP, '/dp/')
def check_amazon(sess, dp):
    sys.stderr.write('[info] check_amazon dp=%s\n' % dp)
    product_uri = urllib.parse.urljoin(AMAZON_DP, dp)

    try_num = 0
    while True:
        try:
            result = sess.get(product_uri, headers = amazon_headers)
            amazon_headers["referer"] = product_uri
            result.raise_for_status()
            
            product_lxml = lxml.html.fromstring(result.text)
            price_td_ary = product_lxml.cssselect('tr.kindle-price> td.a-color-price')

            if len(price_td_ary) != 1:
                raise Exception("amazon html format error")
 
            price_td = price_td_ary[0]
            price_innerhtml = lxml.etree.tostring(price_td).decode()

            price_pattern = re.compile('&#65509;\\s*([0-9,]+)')
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

            unlimited = ('読み放題で読む' in result.text)

            return (price_num, point_num, unlimited)
            # break

        except Exception as e:
            try_num += 1
            if try_num == max_try:
                # todo: implement exception class to retain result.content
                # send_alert_mail(inspect.currentframe(), attach_html=result.content)
                raise e
            sys.stderr.write("[info] retry access in %ss\n" % sleep_duration)
            time.sleep(sleep_duration)
            continue
        
# sigint_caught = 0
# def sigint_handler(signum, frame):
#     sigint_caught += 1
#     if sigint_caught > 5:
#         sys.exit()

def main():
    amazon_cookie = os.environ.get("AMAZON_COOKIE")
    if amazon_cookie:
        amazon_headers["cookie"] = amazon_cookie
    
    list_id = os.environ['AMAZON_WISH_LIST_ID']
    pg_url = os.environ['DATABASE_URL']
    table_name = 'generic_text_data'
    key_name = 'kindle_price'

    amazon_sess = requests.session()    
    pg_conn = psycopg2.connect(pg_url)
    pg_cur = pg_conn.cursor()
    kindle_price_data = pg_init_json(pg_cur, table_name, key_name)
    # pg_cur.close()
    # pg_conn.commit()
    # pg_conn.close()

    messages = []
    # kindle_price_data_new = {}
    # check_progres = False
    exc = None
    exc_tb = None
    skip_list = []
    # signal.signal(signal.SIGINT, sigint_handler)
    try:
        for item in get_wish_list(amazon_sess, list_id):
            dp = item['dp']
            item_title = item['title']

            if dp not in kindle_price_data:
                prev_net_price = -1
                prev_unlimited = False
                date_prev = None
            else:
                prev_net_price = kindle_price_data[dp]["price"] - kindle_price_data[dp]["point"]
                prev_unlimited = kindle_price_data[dp].get("unlimited")
                date_prev = datetime.datetime.strptime(kindle_price_data[dp].get("date"), "%Y/%m/%d %H:%M:%S")

            datetime_now = datetime.datetime.now()
            min_skip = 30
            if date_prev and ((date_prev + datetime.timedelta(minutes=min_skip)) > datetime_now):
                skip_list.append(dp)
                # sys.stderr.write("[info] skipping %s since this is checked within %s minutes\n" % (dp, min_skip) )
                # sys.stderr.write("[info] %s %s\n" % (date_prev, datetime_now) )
                continue
            else:
                if len(messages)>0:
                    sys.stderr.write("[info] skipped following since these are checked within %s minutes:\n%s\n" % (min_skip, ", ".join(skip_list)) )
                    skip_list = []
            new_state = check_amazon(amazon_sess, dp)
            new_net_price = new_state[0] - new_state[1]
            unlimited = new_state[2]
            sys.stderr.write('[info] price=%s point=%s net_price=%s unlimited=%s\n' % (new_state[0], new_state[1], new_net_price, unlimited))

            if new_net_price != prev_net_price or prev_unlimited != unlimited:
                mes = "<a href=\"%s\">%s</a> %s %s<- %s" % (urllib.parse.urljoin(AMAZON_DP, dp), item_title, new_net_price, ("unlimited " if unlimited else ""), prev_net_price)
                messages.append(mes)
                sys.stderr.write("[info] %s\n" %mes)
            
            kindle_price_data[dp] = { \
                "title": item_title, \
                "price": new_state[0], \
                "point": new_state[1], \
                "unlimited": new_state[2], \
                "date": datetime_now.strftime("%Y/%m/%d %H:%M:%S") \
            }
            
        if len(skip_list)>0:
            sys.stderr.write("[info] skipped following since these are checked within %s minutes:\n%s\n" % (min_skip, ", ".join(skip_list)) )
    except Exception as e:
        if date_prev and ((date_prev + datetime.timedelta(hours=3)) < datetime_now):
            exc_tb = traceback.format_exc()
            exc = e

    amazon_sess.close()

    if len(messages)>0:
        send_mail("<br />\n".join(messages), "Update of Kindle Price")

    # pg_conn = psycopg2.connect(pg_url)
    # pg_cur = pg_conn.cursor()
    pg_update_json(pg_cur, table_name, key_name, kindle_price_data)
    pg_cur.close()
    pg_conn.commit()
    pg_conn.close()

    if exc:
        raise exc
    elif exc:
        sys.stderr.write(exc_tb)

if __name__ == '__main__':
    main()
