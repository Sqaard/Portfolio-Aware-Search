# Defence speech — BigData FinPortfolio IR

Target: 10–12 minutes, one block per slide. **Bold** is what you should not skip.
*Italic* marks an analogy — tell it in your own words, don't read it.

---

## Slide 1 — Title (20 sec)

Good afternoon. My name is Ivan, and the project is FinPortfolio IR.

In one sentence: it is **a search engine over financial documents** that answers
not "what do we know now", but **"what was knowable on a particular date"**.
Today I will mostly talk about how the data processing inside it works.

---

## Slide 2 — What the project is (1 min)

Picture an investor. They make a decision on 15 March 2015, and they want to
base it on documents: company filings, macro statistics, official press releases.

There is a trap here, and it is the central one in this whole topic.

> *It is like marking your own homework while peeking at the answer key the
> teacher will only publish tomorrow. On paper you look brilliant. In reality
> you just copied.*

If the engine shows that investor a document published on **17 March** while the
decision is made on the **15th**, then every backtest built on it is a lie. So
every document stores the date it **actually became available**, and search never
looks past it.

The project has three parts: collect the data, process it, extract value. The
middle part — processing — is the Big Data layer, and that is the main story.

---

## Slide 3 — Sources (40 sec)

Data from three worlds, all public and official:

- **macro statistics** — 18,240 releases: rates, inflation, unemployment, oil;
- **SEC filings** — 7,011 sections from 10-K, 10-Q and 8-K;
- **company news** — 1,117 documents from official corporate newsrooms.

The important part: every document carries its source, its URL, its timestamps
and a stable hash. **Any document can be traced back to its origin** — this is
not a pile of text scraped from somewhere.

---

## Slide 4 — The crawler (1 min)

How documents get in.

> *Think of a postman with a verified address book — not "google where Apple
> keeps its news", but specific addresses checked by hand.*

Five steps, left to right on the diagram:

1. **Source registry** — 139 hand-verified addresses across 29 companies, plus
   12 official source families such as SEC and FRED.
2. **Fetcher** — pulls HTML, RSS and filings from SEC EDGAR.
3. **Normalizer** — turns everything into one shape. Whatever the source, the
   output has the same fields: ticker, source type, stable hash.
4. **Point-in-time ledger** — the part that makes peeking into the future
   impossible.
5. **Automatic updates** — a re-run **appends only new documents**. Run it twice
   and nothing breaks and nothing is counted twice.

---

## Slide 5 — What the data really is (1 min 30 sec)

The corpus Spark reads is **352 megabytes, 26,368 documents**, in one file. On
the left you can see what is inside it.

- **SEC filing sections** — 6,356 documents, but **254 megabytes**. Three
  quarters of the whole volume. One 10-K section is about 41 kilobytes of text.
- **Official macro releases** — 18,240 documents, but only 38 megabytes.
- **SEC exhibits** — 655 documents, 26 megabytes.
- **Company IR documents** — 1,117 of them, 18 megabytes.

Look at the box at the bottom: **macro is 69% of the documents but only 11% of
the bytes**. One macro release is 2 kilobytes; one 10-K section is 41. So "how
many documents" and "how much data" are two different answers, and you need both
to plan the work: the document count drives the number of tasks, the volume
drives how much travels over the network.

**On the right is one real document**, exactly as it sits in the file. Nothing
invented — this is a line from the corpus: the "Item 1A. Risk Factors" section of
Apple's 2021 annual report. Nine of its 55 fields are shown; the whole thing does
not fit on a slide.

Look at the provenance chain, this is the important part:

- `url` — the direct sec.gov link the file came from;
- `sec_accession_number` — the SEC filing's registration number;
- `parent_doc_id` — **the whole filing** this section was cut out of;
- `sec_section_code: 1A · chars 24,654–91,275 of 223,208` — the exact character
  range the section occupies inside that filing.

So any document can be traced backwards: here is the section, here is the filing,
here is the accession number, here is the link, here are the exact characters.
This is not "we downloaded some text from somewhere".

And the `available_at` field: 29 October 2021. Search filters on **that alone** —
on 28 October this document cannot be found.

**Now the bottom row — what one Spark run does with all this.**

> *Imagine you have to build an index for a book. The book itself is thin. But to
> build the index you have to write every single word onto its own card — and the
> pile of cards ends up many times heavier than the book. Sorting that pile is
> the work. Carrying the book is not.*

- 352 megabytes go in;
- the job writes out **45 million cards** — "term — document" pairs;
- that is **1,709 pairs per document**, and all of them have to be shuffled
  between machines so that identical words end up in the same hands;
- 79,705 terms come out — a **565-fold collapse**.

And let me answer the obvious objection myself: 352 megabytes is **not big data
by volume**. There is nothing to argue about there. But the problem is not the
size of the file — it is that in the middle the data expands a thousandfold and
needs a full shuffle, and that happens on every corpus update. That is the work a
distributed engine is for.

---

## Slide 6 — Why Spark (40 sec)

I chose between three.

- **Hadoop MapReduce** — the same model, but written in Java and it writes to
  disk after every step. Slow and heavy.
- **Storm** — streaming only, and painful to run on Windows.
- **Spark** — does exactly what I need: MapReduce, plus SQL, plus streaming, and
  it works from Python.

I took Spark. And, importantly, **the same code** runs on a laptop and on a
cluster — one line changes.

---

## Slide 7 — One job, two deployments (1 min)

That one line. At the top is the job code: `map`, `flatMap`, `reduceByKey`.
It **does not change at all**.

Only the address differs: `local[*]` — "run on my laptop" — or
`spark://spark-master:7077` — "run on the cluster".

> *It is the same recipe. You can cook it alone at home, or hand it to a brigade
> in a restaurant. The recipe is identical; only who stands at the stove changes.*

On the left, the laptop: one JVM, 12 cores, and a fresh `python.exe` started for
every task. On the right, the Docker cluster: a master and two workers, 4 cores
in total, where a new process is created with `fork` and costs almost nothing.

**66.8 seconds against 4.0.** With three times fewer cores. Why — that is slide 12.

At the bottom, the key line: **the output is byte-identical**. Not "roughly the
same" — literally the same bytes.

---

## Slide 8 — From a URL to an inverted index (2 min, the key slide)

This is the heart of the work. I will go slowly.

**What actually has to be computed.** For search you need to know in how many
documents each word appears. "oil" appears in 3,937 documents, "price" in 7,103.
A rare word is valuable, a common one is not. The whole ranking formula rests
on this.

**The top row — how the text appears at all.** Take an address from the verified
registry → download and cache the page → pull out the title, body and dates with
a regular expression → write it into one file. 352 megabytes, 26,368 documents.

**Now the interesting part.** The file is cut into 12 pieces and the pieces are
handed to the workers.

> *Imagine a classroom and a library of 26,000 books. You need to know in how
> many books the word "oil" appears.*
>
> *One child would be counting until evening. So call two, split the books in
> half. Each keeps their own tally: the first has "oil — 1875", the second has
> "oil — 2062".*
>
> *Now we have to add them up. And here is the trap: if they both start shouting
> their numbers at each other, it turns into a mess and something gets
> double-counted.*
>
> *So we agree IN ADVANCE: every card with the word "oil" goes to Anna, every
> card with "price" goes to Boris. How do we know who to bring it to? We compute
> it from the word itself with a simple rule — take a remainder. The rule is the
> same for everyone, so one word always lands with the same person. Always.*
>
> *Anna adds up only her pile, Boris only his. They do not get in each other's
> way, and they cannot double-count.*

That is the three stages on the diagram:

- **Map** — each worker reads its own documents and writes out "word — 1" pairs.
  Inside its own piece it immediately merges duplicates: `(oil,1) + (oil,2)`
  becomes `(oil,3)`. This saves network traffic.
- **Shuffle** — the arrows **crossing between the workers**. That is the
  regrouping: a word goes to "its" worker, no matter where it was produced. The
  rule is `hash(term) % numPartitions`.
- **Reduce** — each worker sums what arrived. We get `(oil, 3937)` and
  `(price, 7103)`.

Out comes the inverted index and the statistics the ranking formula needs.

**Why not just do it in one process?** You can — on 24 documents. On 26,000 it
takes minutes, and the corpus is updated constantly. And more importantly: this
way **does not hit the ceiling of one machine** — add workers, it gets faster,
and the code does not change.

---

## Slide 9 — Scale (30 sec)

Concrete numbers on the macro corpus: 18,240 documents, 5,520 distinct terms,
1.17 million tokens in total, 64 tokens per document on average.

These are exactly the numbers that get compared against the reference.

---

## Slide 10 — Why it is useful (40 sec)

The index is not the goal in itself. From it you immediately see what the corpus
is even about:

6,743 documents about rates, 3,466 about inflation, 3,451 about Fed policy,
coverage from 2010, roughly 1,300 documents a year.

So the analytics come out as a **by-product** of the same job: we already walked
every document and counted everything — all that is left is to group it.

---

## Slide 11 — Verification (1 min)

This, to me, is the most important part, and here is why.

> *If two calculators give different answers to the same sum, both are bad. You
> do not know which one to trust.*

I have a **reference** — an ordinary single-machine implementation of the index,
written earlier and simple enough to check by eye. And I have the distributed
Spark version.

The requirement is strict: **the results must be byte-identical**. Not "within
tolerance" — byte for byte. And they are: term frequencies, document lengths,
average length, document count. Search scores agree to 1e-9.

This is checked automatically: **238 tests**, the whole suite green. Plus a check
that appending new documents gives the same result as recomputing from scratch.

The point is simple: **speed without correctness is worth nothing**. First I
proved the distributed version computes the same thing, and only then did I start
making it faster.

---

## Slide 12 — Windows versus the cluster (2 min)

Here I will tell you something I did not expect to find.

**The observation.** The same code on a 12-core laptop takes **66.8 seconds**;
on a toy 4-core cluster it takes **4.0**. Sixteen times faster with three times
fewer cores. That is strange, and it needed explaining.

**Look at the left table.** Split the work into 2 pieces — 11 seconds. Into 4 —
21 seconds. Into 12 — 67 seconds. **The more we split, the SLOWER it gets.** That
should not happen: parallelism is supposed to help.

> *Every piece of work is one box, and every box needs a worker.*
>
> *On Linux a worker can split in two: an already-trained worker makes a copy of
> himself, and the copy already knows everything. That is almost free.*
>
> *On Windows you cannot do that. For every box you hire a person off the street
> and train them from scratch: start a new process, load all the libraries again.
> And the training takes longer than the work on the box.*
>
> *So the finer you slice, the more you pay for hiring instead of for work.*

**The right table — what to do about it.** Four remedies, none of which requires
writing your own engine:

1. **The advice from the internet does nothing.** The first thing anyone suggests
   is turning on `spark.python.worker.reuse`. Result: 64.1 versus 66.8 seconds —
   nothing. And that makes sense: the setting optimises the very copying
   mechanism **that Windows does not have**. A negative result is still a result.
2. **Slice coarser** — 12 pieces gave 67 seconds, 2 pieces gave 11. Six times
   faster just by not over-splitting.
3. **Move to Linux** — the cluster, 4 seconds. Seventeen times.
4. **Do not hire anyone at all.** Rewrite the same job in SQL, which Spark
   executes inside itself with no Python processes. **1.2 seconds on Windows.**

And here is the conclusion everything was measured for: **the SQL version is
faster on Windows than on the cluster** — 1.2 against 1.9 seconds. Once the
Python workers were gone, the only weakness Windows had disappeared, and the
12-core laptop simply beat the 4-core cluster.

**So the problem was never "Windows is slow".** It was specifically starting a
Python process per task. Those are different claims, and the second one is
testable.

**Then why is the slow version still in the project?** Because the SQL version
tokenises text differently and produces a different vocabulary — 18,158 terms
instead of 5,520. It demonstrates the mechanism, but **it does not give
byte-identical output against the reference**. And that identity is the core
correctness argument. So the choice was deliberate: **provable correctness first,
and take the speed from the cluster**, where it is free.

---

## Slide 13 — Conclusion (40 sec)

Briefly, against the four requirements:

1. **Collection** — a crawler with verified sources and automatic updates.
2. **Big Data** — MapReduce on Spark, running on a Docker cluster.
3. **Analytics** — topic and source coverage, plus a report.
4. **Verification** — byte-identical output against the reference, 238 tests.

One codebase: **the same job runs on a laptop or on a cluster, and the output is
identical.** Thank you — I am ready for questions.

---

# Questions to expect

**— 352 megabytes is not big data.**
Agreed, not by volume. But the problem is not volume, it is shape: a full shuffle
of 45 million pairs on every update, and one machine limits you not by memory but
by the fact that it does not scale. The same code runs 16 times faster on a
cluster with no edits — that is what was tested.

**— Why Spark if one machine is enough?**
One machine is enough today. Running it on a cluster showed that adding workers
needs no code change. And the cluster is what answered the question of where the
Windows slowness came from — on one machine you could not see it.

**— What exactly is "byte-identical"?**
Four artifacts: term frequencies, document lengths, average length, document
count. The files are compared as bytes. Search scores agree to 1e-9, because
those are floating-point numbers.

**— Why does the partition count matter so much?**
A partition is a task, and a task is a process. On Windows each process starts
from scratch (2–5 seconds of imports); on Linux it is a `fork`. With 12 partitions
you pay for 12 start-ups; with 2, for two.

**— Why 12 partitions rather than 128 MB blocks?**
Because 128 MB is a ceiling that never binds here, and 12 is what Spark picks on
its own. Its formula is `min(128 MB, max(4 MB, totalBytes / cores))`. For 352 MB
on 12 cores that is `min(128, max(4, 28)) = 28 MB`, so the 128 MB term loses. I
measured it: the DataFrame reader produces 12 partitions for the full corpus by
itself. Asking for 12 is not overriding Spark, it is naming the number it already
chose. The RDD path answers differently again — `sc.textFile` ignores that setting
and uses Hadoop's 32 MB local split, where `minPartitions` is only a floor: on
352 MB anything below 11 is ignored. And 128 MB per partition would mean **3
partitions**, fewer than the machine has cores, leaving 9 of 12 idle. The rule of
thumb assumes per-task overhead is negligible; on Windows it is not, and there the
fastest configuration turned out to be **2** partitions.

**— Doesn't AQE choose the partition count for you?**
Not here, for two reasons, and I measured both. First, AQE is a Spark **SQL**
feature — it rewrites a query plan using runtime statistics. An RDD job has no
plan, so AQE never sees it: with AQE on the RDD job takes 60.85 s, with it off,
59.84 s. Second, even where AQE does apply it coalesces **post-shuffle**
partitions; it cannot change the number of **map** tasks, and the map side is
where the Windows cost is. What actually drives the curve is task count: the job
has four stages, so 2 partitions means 8 tasks and 12 partitions means 48, at
about 1.3 seconds of Python start-up each — 12 s against 60 s. A control run with
one integer per partition and no real work costs the same ~1 s per task, which is
what proves it is process start-up rather than data.

**— How is a word's value computed?**
The rarer the word, the more valuable:
`idf = ln(1 + (N − df + 0.5)/(df + 0.5))`. It is `df` — the number of documents
containing the word — that MapReduce computes. Plus a length correction, so long
documents do not win automatically.

**— Where do the availability dates come from?**
From ALFRED, the archive that records when each figure was first published.
Earlier the date was estimated, and it turned out that **36% of documents were
marked available earlier than they really were** — for monthly indicators the
error reached three to five weeks. Now the real publication date is used.

---

# Presenter's note — read this before you go in

Slides 7 and 12 quote **4.0 s on the cluster and a 16.7× advantage**. Those were
measured on an idle machine at the time the report was written. Re-measured
recently on the same machine, the cluster gives **6.4 / 7.0 / 8.8 s** for
2 / 4 / 12 partitions, so the advantage comes out around **7×**, not 16.7×. The
Windows baselines still reproduce exactly (12.8 / 23.1 / 64.5 s).

The argument does not depend on the exact multiplier — the shape is what matters
and it is unchanged: **on Windows the time grows with the partition count, on the
cluster it stays flat**. But if you are asked to run it live, be ready to say so
rather than be surprised. If you want the safest option, re-measure on the day
with:

```powershell
.\deploy\run_spark.ps1 bigdata.run_inverted_index --corpus macro --partitions 12
.\deploy\spark_cluster\submit.ps1 bigdata.run_inverted_index --corpus macro --native-read --partitions 12
```

and quote whatever comes out. Full command sequences are in
[`docs/DEFENCE_RUNBOOK.md`](../docs/DEFENCE_RUNBOOK.md).
