从抖音热搜 + 摸摸鱼多平台热榜并行拉取当前热点话题，输出各平台榜单供选题参考。

数据源：抖音热搜（官方接口）+ 知乎、微博、豆瓣、今日头条、B站、贴吧、博客园等（摸摸鱼 RSS），并行拉取。

## 用法

### 默认：并行拉取所有源

```bash
python3 utils/fetch_topics.py
```

### 每平台只看前 N 条

```bash
python3 utils/fetch_topics.py --top 5
```

### 按关键词过滤

```bash
python3 utils/fetch_topics.py --filter $ARGUMENTS --top 10
```

### 仅抖音热搜

```bash
python3 utils/fetch_topics.py --douyin --top 20
```

### 抖音关键词搜索

```bash
python3 utils/fetch_topics.py --douyin-search --search $ARGUMENTS --top 10
```

### B站关键词搜索

```bash
python3 utils/fetch_topics.py --bilibili --search $ARGUMENTS --order click --top 10
```

### JSON 输出（供脚本/Agent 解析）

```bash
python3 utils/fetch_topics.py --json --top 10
```

## 参数说明

| 参数 | 说明 |
|------|------|
| `--top N` | 每平台最多显示 N 条，默认全量 |
| `--filter 词1,词2` | 关键词过滤，任意词命中标题即保留 |
| `--douyin` | 仅抖音热搜榜 |
| `--douyin-search --search 词` | 抖音关键词搜索 |
| `--bilibili --search 词` | B站关键词搜索 |
| `--order` | B站排序：scores/click/pubdate（默认 scores） |
| `--all` | 强制拉取全部非搜索源（默认行为） |
| `--json` | 结构化 JSON 输出 |

执行后展示结果，并总结有哪些值得制作短视频的选题。
