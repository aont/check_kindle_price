* environmental variables
following variables are necessary
 - AMAZON_WISH_LIST_ID=XXXXX
     # https://www.amazon.co.jp/hz/wishlist/ls/XXXXX
 - LINE_TOKEN=fuga1
     # line notify api token
 - TZ=Asia/Tokyo
     # in order to get correct timestamp

to set variables on heroku
$ heroku config:set -a app_name VAR=VALUE
