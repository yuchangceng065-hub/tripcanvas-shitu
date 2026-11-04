"""Deterministic parsing of a multi-condition Hangzhou request.

The parser never invents places: it only returns text mentions that must be
confirmed against real POI search results before planning.
"""
from __future__ import annotations
import re
from datetime import date

INTERESTS=('咖啡','拍照','博物馆','美术','自然','美食','夜景','逛街','公园','书店')
ALIASES={'浙工大':'浙江工业大学','工大':'浙江工业大学'}
STOPWORDS={'杭州','杭州市','西湖区','今天','明天','后天','周末','附近','周边','这边','那里','一下','地方','玩','逛逛'}
NUMERALS={'一':1,'二':2,'两':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9,'十':10}

def _number(token):
    return NUMERALS.get(token) or int(token)

def _clock(hour,minute):
    hour,minute=int(hour),int(minute or 0)
    if not (0<=hour<=23 and 0<=minute<=59):raise ValueError
    return hour*60+minute

def _clean_place(text):
    text=re.sub(r'(看看|逛逛|玩|参观|游览|打卡|一下|吧|呢|的)+$','',text.strip())
    text=re.sub(r'^(去|到|在|想|看看|逛逛)+','',text)
    return text.strip(' 　')

def parse_request(text,today=None):
    today=today or date.today()
    notes=[];requirements={}
    match=re.search(r'(20\d{2})\s*[-/年]\s*(\d{1,2})\s*[-/月]\s*(\d{1,2})\s*日?',text)
    if match:
        try:requirements['startDate']=date(int(match.group(1)),int(match.group(2)),int(match.group(3))).isoformat()
        except ValueError:notes.append('日期无法识别，请手动选择有效的出发日期。')
    else:
        short=re.search(r'(\d{1,2})\s*月\s*(\d{1,2})\s*日',text)
        if short:
            year=today.year
            try:
                candidate=date(year,int(short.group(1)),int(short.group(2)))
                if candidate<today:candidate=date(year+1,candidate.month,candidate.day)
                requirements['startDate']=candidate.isoformat()
                notes.append(f'未写年份，按 {candidate.year} 年理解，请确认日期。')
            except ValueError:notes.append('日期无法识别，请手动选择有效的出发日期。')
    days=re.search(r'([一二两三四五六1-6])\s*(?:天|日游)',text)
    if days:requirements['days']=_number(days.group(1))
    people=re.search(r'([一二两三四五六七八九十]|\d{1,2})\s*(?:个)?人',text)
    if people:requirements['people']=_number(people.group(1))
    if re.search(r'自驾|开车|驾车|租车',text):requirements['mode']='drive'
    elif re.search(r'步行|走路|徒步|少走',text):requirements['mode']='walk'
    if re.search(r'少走|不想走|轻松|不.{0,2}赶|悠闲|休闲|慢',text):requirements['pace']='relaxed'
    elif re.search(r'紧凑|充实|多逛|多去',text):requirements['pace']='full'
    interests=[interest for interest in INTERESTS if interest in text]
    if interests:requirements['interests']=interests
    deadline=re.search(r'(\d{1,2})\s*[:：点]\s*(\d{2})?\s*前?\s*到\s*([^\s，。；、,;]+)',text) or re.search(r'到\s*([^\s，。；、,;]+?)\s*前',text)
    destination=None
    if deadline:
        groups=deadline.groups()
        if len(groups)>=3 and groups[0]:
            try:
                requirements['returnBy']=_clock(groups[0],groups[1])
                destination=_clean_place(groups[2] or '')
            except ValueError:notes.append('返程时间无法识别，请手动填写。')
        else:destination=_clean_place(groups[0] or '')
    origin=re.search(r'从\s*([^\s，。；、,;]+?)\s*(?:出发|开始)',text)
    origin_text=_clean_place(origin.group(1)) if origin else None
    mentions=[];seen=set()
    def mention(value,role):
        value=_clean_place(value)
        if not value or value in STOPWORDS or value in seen:return
        if re.fullmatch(r'[一二两三四五六七八九十\d]{1,3}[天日人个]?',value):return
        if '杭州'==value:return
        seen.add(value);mentions.append({'text':ALIASES.get(value,value),'role':role})
    def add_chunk(chunk):
        chunk=re.split(r'喜欢|想喝|爱吃|顺便',chunk)[0]
        for part in re.split(r'[、,，;；/]|和|跟|与|还有',chunk):
            if part.strip():mention(part,'visit')
    for chunk in re.findall(r'(?:想去|想参观|想逛|想去看看|参观|游览|逛逛|看看|打卡)\s*([^，。；\n]+)',text):add_chunk(chunk)
    for chunk in re.findall(r'去\s*([^，。；、\n]+)',text):
        if not any(stop in chunk for stop in ('杭州','天','日','人','点','前','站前')):add_chunk(chunk)
    if destination:mention(destination,'destination')
    if origin_text:mention(origin_text,'origin')
    evidence={'startDate':'startDate' in requirements,'days':'days' in requirements,'people':'people' in requirements,
        'mode':'mode' in requirements,'pace':'pace' in requirements,'interests':interests,'returnBy':'returnBy' in requirements,
        'destination':bool(destination),'origin':bool(origin_text),'mentions':[m['text'] for m in mentions]}
    missing=[key for key in ('startDate','days','people') if key not in requirements]
    if 'returnBy' in requirements and not destination:missing.append('destination')
    return {'requirements':requirements,'evidence':evidence,'missing':missing,'mentions':mentions,'notes':notes}
