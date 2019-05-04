* environmental variables
following variables are needed
 - AMAZON_GP_ARRAY=hoge1,hoge2,hoge3
     # https://www.amazon.co.jp/dp/XXXXXX
 - LINE_TOKEN=fuga1
     # line notify api token
 - TZ=Asia/Tokyo
     # in order to get correct timestamp

to set variables on heroku
$ heroku config:set -a app_name VAR=VALUE
