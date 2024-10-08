import arrow
import feedparser
import os
import logging
import time

from atproto import Client, client_utils
from atproto_client import models
from bs4 import BeautifulSoup
from dotenv import load_dotenv
import httpx

load_dotenv()


def get_last_bsky(client):
    timeline = client.get_author_feed(os.environ['BLUESKY_HANDLE'])
    for titem in timeline.feed:
        # We only care about top-level, non-reply posts
        if titem.reason == None and titem.post.record.reply == None:
            return arrow.get(titem.post.record.created_at)
    # TODO If we only get replies and reposts we are in trouble!


# https://www.docs.bsky.app/docs/advanced-guides/post-richtext
# https://www.spokenlikeageek.com/2023/11/08/posting-to-bluesky-via-the-api-from-php-part-three-links/
# https://atproto.blue/en/latest/atproto_client/utils/text_builder.html
def make_rich(content):
    text_builder = client_utils.TextBuilder()
    for line in content.lstrip().split("\n"):
        if line.startswith("http"):
            text_builder.link(line, line.strip())
        else:
            text_builder.text(line)
        text_builder.text('\n\n')
    return text_builder


# https://stackoverflow.com/a/66690657
def html_filter(content):
    soup = BeautifulSoup(content, features="html.parser")
    text = ""
    for e in soup.descendants:
        if isinstance(e, str):
            text += e
        elif e.name in ["br", "p", "h1", "h2", "h3", "h4", "tr", "th"]:
            text += "\n"
        elif e.name == "li":
            text += "\n- "
    return text


def mention_filter(content):
    if content.startswith("@"):
        return ""
    else:
        return content


def length_filter(content):
    if len(content) > 256:
        content = content[0:253]
        content += "..."
    return content


FILTERS = [html_filter, length_filter, mention_filter]

client = Client()
client.login(os.environ['BLUESKY_USERNAME'], os.environ['BLUESKY_PASSWORD'])

logging.basicConfig(filename="rss2bsky.log", encoding="utf-8", level=logging.INFO)


def main():
    last_bsky = get_last_bsky(client)
    feed = feedparser.parse(os.environ['RSS_FEED'])

    for item in reversed(feed["items"]):
        rss_time = arrow.get(time.strftime('%Y-%m-%dT%H:%M:%SZ', item["published_parsed"]))
        content = item["summary"]
        for filter_method in FILTERS:
            content = filter_method(content)
        if len(content) > 300:
            logging.warning("Post too long :( %s" % (item["link"]))
        if len(content.strip()) > 0:
            rich_text = make_rich(content)
            rich_text.link("🔗", item["link"])
            if 'tags' in item:
                rich_text.text('\n')
                for tag in item['tags']:
                    rich_text.tag(text=f'#{tag["term"]}', tag=tag['term'])
                    rich_text.text(' ')

            if rss_time > last_bsky:
                try:
                    images = []
                    if 'media_content' in item:
                        media_contents = item['media_content']
                        media_texts = item.get('content', [{'value': ''}]) * len(media_contents)
                        for media_content, media_text in zip(media_contents, media_texts):
                            img_data = httpx.get(media_content['url']).content
                            upload = client.upload_blob(img_data)
                            images.append(models.AppBskyEmbedImages.Image(alt=media_text['value'], image=upload.blob))
                    embed = models.AppBskyEmbedImages.Main(images=images)

                    client.send_post(text=rich_text, embed=embed)
                    logging.info("Sent post %s" % (item["link"]))
                except Exception as e:
                    logging.exception("Failed to post %s" % (item["link"]))
            else:
                logging.debug("Not sending %s" % (item["link"]))


main()
