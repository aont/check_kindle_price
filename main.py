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
import itertools

import requests
import psycopg2
import psycopg2.extras
import requests
import lxml.html
import cssselect
import sendgrid
import sendgrid.helpers


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

def pg_init_json(pg_cur, table_name, kindle_price_key_name):
    pg_result = pg_execute(pg_cur, "select 1 from pg_tables where schemaname='public' and tablename=%s ;", [table_name])
    pg_result = pg_cur.fetchone()
    if pg_result is None:
        #sys.stderr.write('[info] creating table\n')
        pg_execute(pg_cur, "create table %s (key text unique, value text);" % (table_name))
    elif 1 != pg_result[0] :
        raise Exception("exception")

    pg_execute(pg_cur, 'select value from %s where key=%%s;' % table_name, [kindle_price_key_name])
    pg_result = pg_cur.fetchone()
    
    if pg_result is None:
        pg_execute(pg_cur, 'insert into %s VALUES (%%s, %%s);' % table_name, [kindle_price_key_name, "{}"])
        pg_data = {}
    else:
        sys.stderr.write('[info] data=%s\n' % str_abbreviate(pg_result[0]))
        pg_data = json.loads(pg_result[0])
    return pg_data

def pg_update_json(pg_cur, table_name, kindle_price_key_name, pg_data):
    return pg_execute(pg_cur, 'update %s set value = %%s where key = %%s;' % table_name, [json.dumps(pg_data, ensure_ascii=False), kindle_price_key_name])



def get_wish_list_page(sess, list_id, last_evaluated_key_ref):

    last_evaluated_key = last_evaluated_key_ref[0]
    url = urllib.parse.urljoin(AMAZON_LIST, list_id)
    if last_evaluated_key:
        url += "?lek=" + last_evaluated_key

    try_num = 0
    while True:
        try:
            result = sess.get(url, headers = amazon_headers)
            time.sleep(sleep_duration)
            amazon_headers["referer"] = url
            result.raise_for_status()

            if "この画像に見える文字を入力してください" in result.text:
                raise Exception("captcha")

            product_lxml = lxml.html.fromstring(result.text)
            g_items = product_lxml.get_element_by_id('g-items')
            # may raise Exception
            li_ary = g_items.cssselect('li')

            lastEvaluatedKey_elems = product_lxml.cssselect('input.lastEvaluatedKey')
            len_lastEvaluatedKey_elems = len(lastEvaluatedKey_elems)
            if len_lastEvaluatedKey_elems==1:
                last_evaluated_key_ref[0] = lastEvaluatedKey_elems[0].get("value")
            else:
                raise Exception("len(lastEvaluatedKey_elems)=%s" % len_lastEvaluatedKey_elems)

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
                yield (dp_id, item_title)

            break

        except Exception as e:
            # requests.exceptions.RequestException
            try_num += 1
            if try_num == max_try:
                raise e
            sys.stderr.write("[info] retry\n")
            continue



def check_amazon(sess, dp):
    sys.stderr.write('[info] check_amazon dp=%s\n' % dp)
    product_uri = urllib.parse.urljoin(AMAZON_DP, dp)

    try_num = 0
    while True:
        try:
            result = sess.get(product_uri, headers = amazon_headers)
            time.sleep(sleep_duration)
            amazon_headers["referer"] = product_uri
            result.raise_for_status()

            if "この画像に見える文字を入力してください" in result.text:
                raise Exception("captcha")

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
                # sys.stderr.write("[debug]point_innerhtml=%s\n" % point_innerhtml)
                point_pattern = re.compile('([0-9,]+)(pt|point|&#12509;&#12452;&#12531;&#12488;)')
                point_match_obj = point_pattern.search(point_innerhtml)
                if point_match_obj is not None:
                    point_num = int(point_match_obj.group(1).replace(',',''))

            unlimited = ('読み放題で読む' in result.text)

            return (price_num, point_num, unlimited)
            # break
        except requests.exceptions.RequestException as e:
            try_num += 1
            if try_num == max_try:
                raise e
            sys.stderr.write("[info] retry\n")
            continue

#### ---- main ----

if __name__ == '__main__':

    sleep_duration = 5
    max_try = int(os.environ.get('MAX_TRY', default="5"))

    AMAZON_CO_JP='https://www.amazon.co.jp/'
    AMAZON_LIST=urllib.parse.urljoin(AMAZON_CO_JP, '/hz/wishlist/ls/')
    AMAZON_DP= urllib.parse.urljoin(AMAZON_CO_JP, '/dp/')
    amazon_headers = {
        'authority': 'www.amazon.co.jp',
        'upgrade-insecure-requests': '1',
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/79.0.3945.130 Safari/537.36',
        'dnt': '1',
        'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3',
        'accept-encoding': 'identity',
        'accept-language': 'ja-JP,ja;q=0.9,en-US;q=0.8,en;q=0.7',
        'referer': AMAZON_CO_JP,
    }

    amazon_cookie = os.environ.get("AMAZON_COOKIE")
    if amazon_cookie:
        amazon_headers["cookie"] = amazon_cookie

    list_id = os.environ['AMAZON_WISH_LIST_ID']
    pg_url = os.environ['DATABASE_URL']
    hour_skip = int(os.environ.get('SKIP_DURATION_H', default="4"))
    hour_alert_str = os.environ.get('ALERT_DURATION_H', default="8")
    hour_alert = datetime.timedelta(hours=int(hour_alert_str))
    max_check = int(os.environ.get('MAX_CHECK', default="3"))
    generic_text_data_name = 'generic_text_data'
    ckp_state_name = 'ckp_state'
    kindle_price_name = 'kindle_price'
    date_format = "%Y/%m/%d %H:%M:%S"
    init_date_str = "1970/1/1 00:00:00"
    init_date = datetime.datetime.strptime(init_date_str, date_format)


def main_update_list():

    amazon_sess = requests.session()    
    pg_conn = psycopg2.connect(pg_url)
    pg_cur = pg_conn.cursor()
    kindle_price_data = pg_init_json(pg_cur, 'generic_text_data', 'kindle_price')
    ckp_state = pg_init_json(pg_cur, generic_text_data_name, ckp_state_name)
    last_evaluated_key = ckp_state.get("last_evaluated_key")
    last_evaluated_key_ref = [last_evaluated_key]
    wish_list = ckp_state.get("wish_list")
    if wish_list is None:
        wish_list = {}
        ckp_state["wish_list"] = wish_list
    exc = None
    update_complete = False
    for nc in range(max_check):
        try:
            sys.stderr.write("[info] last_evaluated_key: %s\n" % last_evaluated_key)
            for dp_id, item_title in get_wish_list_page(amazon_sess, list_id, last_evaluated_key_ref):
                wish_list[dp_id] = item_title
                if dp_id not in kindle_price_data:
                    kindle_price_data[dp_id] = {
                        "title": item_title,
                        "date": init_date_str
                    }
            last_evaluated_key = last_evaluated_key_ref[0]
            if last_evaluated_key is None:
                update_complete = True
                break
        except KeyboardInterrupt as e:
            sys.stderr.write(traceback.format_exc())  
            break
        except Exception as e:
            sys.stderr.write("[warn] exception\n")
            check_date_str = ckp_state.get("check_date")
            check_date = datetime.datetime.strptime(check_date_str, date_format)
            datetime_now = datetime.datetime.now()
            if (check_date is None) or ((check_date+hour_alert)<datetime_now):
                exc = e
                break
            else:
                sys.stderr.write(traceback.format_exc())
                break

    ckp_state["last_evaluated_key"] = last_evaluated_key
    if update_complete:
        # tuple(...) is necessary since we delete item(s).
        for dp, kpd_item in tuple(kindle_price_data.items()):
            if dp not in wish_list:
                del kindle_price_data[dp]
        datetime_now = datetime.datetime.now()
        ckp_state["check_date"] = datetime_now.strftime(date_format)

    pg_update_json(pg_cur, generic_text_data_name, ckp_state_name, ckp_state)
    pg_update_json(pg_cur, generic_text_data_name, kindle_price_name, kindle_price_data)

    pg_cur.close()
    pg_conn.commit()
    pg_conn.close()

    if exc:
        raise exc
    return 0

def main_check_price():

    amazon_sess = requests.session()    
    pg_conn = psycopg2.connect(pg_url)
    pg_cur = pg_conn.cursor()
    kindle_price_data = pg_init_json(pg_cur, generic_text_data_name, kindle_price_name)

    exc = None
    date_oldest = None
    for kindle_dp, kindle_item in kindle_price_data.items():
        date_str = kindle_item.get("date")
        if date_str:
            date_datetime = datetime.datetime.strptime(date_str, date_format)
            if date_oldest:
                if date_datetime < date_oldest:
                    date_oldest = date_datetime
            else:
                date_oldest = date_datetime

    messages = []
    skip_list = []

    try:
        kpd_sort = sorted(kindle_price_data.items(), key=lambda x: datetime.datetime.strptime(x[1]["date"], "%Y/%m/%d %H:%M:%S"))

        for dp, kpd_item in itertools.islice(kpd_sort, max_check):

            prev_price = kpd_item.get("price")
            item_title = kpd_item.get("title")
            if prev_price:
                prev_net_price = prev_price - kpd_item["point"]
                prev_unlimited = kpd_item.get("unlimited")
            else:
                prev_net_price = -1
                prev_unlimited = False

            new_state = check_amazon(amazon_sess, dp)
            new_net_price = new_state[0] - new_state[1]
            unlimited = new_state[2]
            sys.stderr.write('[info] price=%s point=%s net_price=%s unlimited=%s\n' % (new_state[0], new_state[1], new_net_price, unlimited))

            if (new_net_price != prev_net_price) or (prev_unlimited != unlimited):
                mes = "<a href=\"%s\">%s</a> %s %s<- %s" % (urllib.parse.urljoin(AMAZON_DP, dp), item_title, new_net_price, ("unlimited " if unlimited else ""), prev_net_price)
                messages.append(mes)
                sys.stderr.write("[info] %s\n" %mes)
            
            datetime_now = datetime.datetime.now()
            kpd_item["price"] = new_state[0]
            kpd_item["point"] = new_state[1]
            kpd_item["unlimited"] = new_state[2]
            kpd_item["date"] = datetime_now.strftime(date_format)

        if len(skip_list)>0:
            sys.stderr.write("[info] skipped following:\n%s\n" % (", ".join(skip_list)) )
    except KeyboardInterrupt as e:
        sys.stderr.write(traceback.format_exc())            
    except Exception as e:
        sys.stderr.write("[warn] exception\n")
        datetime_now = datetime.datetime.now()
        if (date_oldest>init_date) and ((date_oldest + hour_alert) < datetime_now):
            exc = e
        else:
            sys.stderr.write(traceback.format_exc())

    amazon_sess.close()

    if len(messages)>0:
        send_mail("<br />\n".join(messages), "Update of Kindle Price")
    pg_update_json(pg_cur, generic_text_data_name, kindle_price_name, kindle_price_data)
    
    pg_cur.close()
    pg_conn.commit()
    pg_conn.close()

    if exc:
        raise exc

    return 0

if __name__ == '__main__':
    method = sys.argv[1]
    if method == "check_price":
        sys.exit(main_check_price())
    elif method == "update_list":
        sys.exit(main_update_list())
    else:
        sys.stderr.write("[error] unknown method %s\n" % method)
        sys.exit(-1)