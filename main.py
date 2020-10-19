#!/usr/bin/env python
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
import collections
import random

import requests
import psycopg2
import psycopg2.extras
import requests
import lxml.html
import cssselect
import sendgrid
import sendgrid.helpers

def send_mail(message_str, subject, attach_html=None):
    sys.stderr.write("[info] mailing via sendgrid\n")
    sg_from = os.environ["SENDGRID_FROM"]
    sg_recipient = os.environ["SENDGRID_RECIPIENT"]
    sg_apikey = os.environ["SENDGRID_API_KEY"]
    sg_client = sendgrid.SendGridAPIClient(sg_apikey)

    sg_from = sendgrid.Email(name="Check Kindle Price", email=sg_from)
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


dp_pattern = re.compile('/dp/(.*?)/')
def get_wish_list_page(list_id, last_evaluated_key_ref):

    last_evaluated_key = last_evaluated_key_ref[0]
    url = urllib.parse.urljoin(AMAZON_LIST, list_id)
    url += "?ajax=true"
    if last_evaluated_key:
        url += "&lek=" + last_evaluated_key

    try_num = 0
    while True:
        try:
            rotate_cookie()
            result = requests.get(url, headers = amazon_headers)
            time.sleep(sleep_duration)
            amazon_headers["referer"] = url
            result.raise_for_status()

            if b"Amazon CAPTCHA" in result.content:
                raise Exception("captcha")

            try:
                lxml_input = result.content.decode("utf-8")
            except UnicodeDecodeError:
                lxml_input = result.content
            product_lxml = lxml.html.fromstring(lxml_input)

            
            page_title_elem = product_lxml.find(".//title")
            page_title = None
            if page_title_elem is not None:
                page_title = page_title_elem.text
                sys.stderr.write("[info] page_title=%s\n" % page_title)
            else:
                sys.stderr.write("[info] no page_title\n")

            g_items = product_lxml.get_element_by_id('g-items')
            # may raise Exception
            li_ary = g_items.cssselect('li')

            lastEvaluatedKey_elems = product_lxml.cssselect('input.lastEvaluatedKey')
            len_lastEvaluatedKey_elems = len(lastEvaluatedKey_elems)
            if len_lastEvaluatedKey_elems==1:
                last_evaluated_key_ref[0] = lastEvaluatedKey_elems[0].get("value")
            else:
                raise Exception("len(lastEvaluatedKey_elems)=%s" % len_lastEvaluatedKey_elems)

            for li in li_ary:
                data_itemid = li.get("data-itemid")

                itemname_elem = li.get_element_by_id('itemName_%s' % data_itemid)
                item_title = itemname_elem.get('title')
                item_href = itemname_elem.get('href')
                dp_match = dp_pattern.search(item_href)
                if dp_match is None:
                    raise Exception("unexpected")

                dp_id = dp_match.group(1)

                item_byline = li.get_element_by_id('item-byline-%s' % data_itemid).text
                if (item_byline is None) or ("(Kindle版)" not in item_byline):
                    sys.stderr.write("[warn] dp=%s is not Kindle item: %s\n" % (dp_id, item_byline))
                    continue

                yield (dp_id, item_title)

            break

        except Exception as e:
            # requests.exceptions.RequestException
            try_num += 1
            if try_num == max_try:
                raise e
            sys.stderr.write(traceback.format_exc())
            sys.stderr.write("[info] retry\n")
            continue

def iter_match(pat, s):
    while True:
        m = pat.search(s)
        if not m:
            break
        yield m
        s = s[m.end():]

def reduce_same(*args):
    args_valid = filter(lambda x: x is not None, args)
    args_set = tuple(set(args_valid))
    if len(args_set)==0:
        return None
    elif len(args_set)>1:
        raise Exception("multiple values: %s" % repr(args_set))
    else:
        return args_set[0]

price_pattern = re.compile('(?:￥|\\\\)\\s*([0-9,]+)')
point_pattern = re.compile('([0-9,]+)(?:pt|point|ポイント)')
point_pattern_prefix = re.compile('獲得ポイント: ([0-9,]+)(?:pt|point|ポイント)')



def check_amazon(dp):
    sys.stderr.write('[info] check_amazon dp=%s\n' % dp)
    product_uri = urllib.parse.urljoin(AMAZON_DP, dp)

    try_num = 0
    while True:
        try:
            rotate_cookie()
            result = requests.get(product_uri, headers = amazon_headers)
            time.sleep(sleep_duration)
            amazon_headers["referer"] = product_uri
            result.raise_for_status()

            # check captcha
            if b"Amazon CAPTCHA" in result.content:
                raise Exception("captcha")

            # with open("%s.html"%dp, "wb") as f:
            #     f.write(result.content)
            try:
                lxml_input = result.content.decode("utf-8")
            except UnicodeDecodeError:
                lxml_input = result.content
            product_lxml = lxml.html.fromstring(lxml_input)

            page_title_elem = product_lxml.find(".//title")
            page_title = None
            if page_title_elem is not None:
                page_title = page_title_elem.text
                sys.stderr.write("[info] page_title=%s\n" % page_title)
            else:
                sys.stderr.write("[info] no page_title\n")

            if '警告：アダルトコンテンツ' == page_title:
                sys.stderr.write("[info] blackcurtain\n")
                for anchor in product_lxml.iterfind(".//a"):
                    if anchor.text == "［はい］":
                        product_uri = anchor.get("href")
                        break
                else:
                    raise Exception("unable to find yes link")
                continue

            ## sanity check
            try:
                product_lxml.get_element_by_id("title")
            except KeyError as e:
                raise Exception("unable to find title") from e

            ## Check whether kindle item or not
            try:
                title_elem = product_lxml.get_element_by_id("title")
            except KeyError as e:
                raise Exception("#title not found") from e
            title_text = title_elem.text_content()
            if "Kindle版" not in title_text:
                raise Exception("#title does not contain Kindle版: %s" % repr(title_text) )

            # you_pay_section = product_lxml.get_element_by_id('youPaySection')
            # price_num_0 = int(float(you_pay_section.get("data-kindle-price")))

            price_td_ary = product_lxml.cssselect('tr.kindle-price > td.a-color-price')
            # price_td_ary = product_lxml.cssselect('.swatchElement.selected .a-color-price')
            if len(price_td_ary) != 1:
                raise Exception("multiple %s price elements found" % len(price_td_ary))

            price_td = price_td_ary[0]
            price_innerhtml = price_td.text_content()

            price_match_obj = price_pattern.search(price_innerhtml)
            if price_match_obj:
                price_num_1 = int(price_match_obj.group(1).replace(',',''))
            else:
                raise Exception("price text error %s" % repr(price_innerhtml))

            point_td_ary = product_lxml.cssselect('tr.loyalty-points > td.a-align-bottom')
            if len(point_td_ary) > 1:
                raise Exception("%s point elements found" % len(point_td_ary))
            elif len(point_td_ary) == 0:
                point_num_1 = None
            elif len(point_td_ary) == 1:
                point_td = point_td_ary[0]
                point_innerhtml = point_td.text_content()
                point_match_obj = point_pattern.search(point_innerhtml)
                if point_match_obj:
                    point_num_1 = int(point_match_obj.group(1).replace(',',''))
                else:
                    raise Exception("point text error %s" % repr(point_innerhtml))

            swatch_elem_selected_ary = product_lxml.cssselect("li.swatchElement.selected")
            if len(swatch_elem_selected_ary)!=1:
                raise Exception("number of swatch_elem_selected_ary is not 1")
            swatch_elem_selected = swatch_elem_selected_ary[0]
            swatch_elem_selected_text = lxml.etree.tostring(swatch_elem_selected, pretty_print=False, encoding="unicode") #.decode("UTF-8")
            unlimited1 = "Kindle Unlimited" in swatch_elem_selected_text
            # sys.stderr.write("[info] swatch_elem_selected_text=%s\n" % swatch_elem_selected_text)

            price_match_ary = tuple(iter_match(price_pattern, swatch_elem_selected_text))
            price_set = set(int(price_match.group(1).replace(",","")) for price_match in price_match_ary)
            if len(price_set)==0:
                price_num_2 = None
            elif len(price_set)==1:
                price_num_2 = tuple(price_set)[0]
            elif len(price_set)==2:
                price_num_2 = None
                for price in price_set:
                    if price != 0:
                        price_num_2 = price
                        break
                else:
                    raise Exception("found prices: %s " % ", ".join(tuple(price_set)) )
            else:
                raise Exception("found prices: %s " % ", ".join(tuple(price_set)) )

            point_prefix_match = point_pattern_prefix.search(swatch_elem_selected_text)
            if point_prefix_match:
                point_num_2 = int(point_prefix_match.group(1).replace(',',''))
            else:
                point_num_2 = None

            buy_one_click = product_lxml.get_element_by_id('buyOneClick')
            buy_one_click_text = lxml.etree.tostring(buy_one_click, pretty_print=False, encoding="unicode") #.decode()
            unlimited3 = "読み放題で読む" in buy_one_click_text
            # sys.stderr.write("[info] buy_one_click.text_content()=%s\n" % buy_one_click_text)

            # buy_one_click = product_lxml.get_element_by_id('buyOneClick')
            # for input_elem in buy_one_click.iter():
            #     if input_elem.get("name") == "displayedPrice":
            #         price_num_3 = int(float(input_elem.get("value")))
            #         break
            # else:
            #     raise Exception("unable to find #buyOneClick -> displayedPrice")
            price_num_3 = int(float(product_lxml.find('.//*[@id=\'buyOneClick\']/*[@name=\'displayedPrice\']').get("value")))

            point_num = reduce_same(point_num_1, point_num_2)
            if point_num is None:
                point_num = 0

            price_num = reduce_same(price_num_1, price_num_2, price_num_3)
            if price_num is None:
                raise Exception("unable to find price")

            unlimited = unlimited1 or unlimited3
            # unlimited = (b'Kindle Unlimitedのロゴ' in result.content)

            return (price_num, point_num, unlimited)
            # break
        except Exception as e:
            # requests.exceptions.RequestException
            tb = traceback.format_exc()
            try_num += 1
            if try_num == max_try:
                raise e
            sys.stderr.write(tb)
            sys.stderr.write("[info] retry\n")
            continue

def rotate_cookie():
    amazon_cookies.rotate()
    amazon_headers["cookie"] = amazon_cookies[0]


#### ---- main ----

if __name__ == '__main__':

    sleep_duration = int(os.environ.get('SLEEP_DUR', default="5"))
    max_try = int(os.environ.get('MAX_TRY', default="5"))

    AMAZON_CO_JP='https://www.amazon.co.jp/'
    AMAZON_LIST=urllib.parse.urljoin(AMAZON_CO_JP, '/hz/wishlist/ls/')
    AMAZON_DP= urllib.parse.urljoin(AMAZON_CO_JP, '/dp/')
    
    amazon_headers = {
        'authority': 'www.amazon.co.jp',
        'upgrade-insecure-requests': '1',
        'dnt': '1',
        'accept-language': 'ja-JP,ja;q=0.9,en-US;q=0.8,en;q=0.7',
    }
    user_agent = os.environ.get("USER_AGENT")
    if user_agent:
        amazon_headers["user-agent"] = user_agent
    
    # amazon_cookie = os.environ.get("AMAZON_COOKIE")
    # if amazon_cookie:
    #     amazon_headers["cookie"] = amazon_cookie

    amazon_cookies = list()
    i = 0
    while True:
        amazon_cookie_i = os.environ.get("AMAZON_COOKIE%s" % i)
        if amazon_cookie_i is None:
            sys.stderr.write("[info] #amazon_cookies = %s\n" % i)
            break
        amazon_cookies.append(amazon_cookie_i)
        i += 1
    amazon_cookies = collections.deque(random.sample(amazon_cookies, len(amazon_cookies)))
    amazon_headers["cookie"] = amazon_cookies[0]

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

    # amazon_sess = requests.session()
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
            for dp_id, item_title in get_wish_list_page(list_id, last_evaluated_key_ref):
                wish_list[dp_id] = item_title
                if dp_id not in kindle_price_data:
                    kindle_price_data[dp_id] = {
                        "title": item_title,
                        "date": init_date_str
                    }
            last_evaluated_key = last_evaluated_key_ref[0]
            if last_evaluated_key is None:
                sys.stderr.write("[info] update complete\n")
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
        del ckp_state["wish_list"]

    pg_update_json(pg_cur, generic_text_data_name, ckp_state_name, ckp_state)
    pg_update_json(pg_cur, generic_text_data_name, kindle_price_name, kindle_price_data)

    pg_cur.close()
    pg_conn.commit()
    pg_conn.close()

    if exc:
        raise exc
    return 0

def main_check_price():

    # amazon_sess = requests.session()
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
            if prev_price is not None:
                prev_net_price = prev_price - kpd_item["point"]
                prev_unlimited = kpd_item.get("unlimited")
            else:
                prev_net_price = -1
                prev_unlimited = False

            new_state = check_amazon(dp)

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

    # amazon_sess.close()

    if len(messages)>0:
        send_mail("<br />\n".join(messages), "Update of Kindle Price")
    pg_update_json(pg_cur, generic_text_data_name, kindle_price_name, kindle_price_data)

    pg_cur.close()
    pg_conn.commit()
    pg_conn.close()

    if exc:
        raise exc

    return 0

def main_test_sendgrid():
    send_mail("test mail", "test title")
    return 0

def main_test_check_price():
    # amazon_sess = requests.session()
    dp = os.environ['TEST_DP']
    new_state = check_amazon(dp)
    sys.stderr.write("[debug] %s\n" % repr(new_state))
    return 0

if __name__ == '__main__':
    method = sys.argv[1]
    if method == "check_price":
        sys.exit(main_check_price())
    elif method == "update_list":
        sys.exit(main_update_list())
    elif method == "test_sendgrid":
        sys.exit(main_test_sendgrid())
    elif method == "test_check_price":
        sys.exit(main_test_check_price())
    else:
        sys.stderr.write("[error] unknown method %s\n" % method)
        sys.exit(-1)
