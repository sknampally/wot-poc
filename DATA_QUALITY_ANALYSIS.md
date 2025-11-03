# Data Quality Analysis - Current Inputs to APIs

## Overview
Current accuracy: **42.7%** (53/124 fields matching across 4 projects)

## Focus Fields for Improvement
1. **Status** (Announced vs Launched mismatch)
2. **Project Announcement Date** (wrong years extracted)
3. **Project Launch Date** (wrong years extracted)
4. **Tech Stack Descriptions** (too verbose, not concise)
5. **Mission Statement** (wrong language, paraphrasing)
6. **Uses/endorses ZKP** (Failed to disclose vs True)

---

## 1. STATUS Field

### Current State
- **Client**: "Launched"
- **AI**: "Announced"
- **Issue**: AI classifies "Announced" when project is actually "Launched"

### Data Definition (codebook.json)
```json
"Status": {
  "data_definition": "Status could be:\n1. Announced: It has been publicly stated that the project is going to be developed.\n2. Pilot: currently in testing stages before being publicly launched more widely.\n3. Launched: it is already active and working.\n4. Discontinued: project was declared discontinued publicly.",
  "response_type": "[Options] {Announced, Pilot, Launched, Discontinued}"
}
```

### SerpAPI Queries (NO SPECIFIC STATUS QUERIES)
**Current queries for cheqd (example):**
1. `site:cheqd.io` (only if known_website provided)
2. `site:cheqd.io about mission company` (only if known_website provided)
3. `"cheqd" digital identity SSI verifiable credentials`
4. `cheqd official website about mission`
5. `cheqd founded established launched announcement`
6. `cheqd funding investors partners`
7. `cheqd github repository open source`
8. `cheqd wallet ZKP zero-knowledge proofs`
9. `cheqd export credentials key storage`

**Issue**: No specific query asking for "status" or "is it launched?"

### LLM Prompt (prompts.json)
**Current prompt snippets:**
```
Status: Use ONLY these values: {status_enums}. 
Look for words like 'launched', 'pilot', 'announced', 'live', 'production'.
```

**Field-specific guidance (fallback_hints):**
```
Status: Use ONLY these values: {status_enums}. 
Look for 'launched', 'live', 'in production', 'general availability (GA)', 'pilot', 'beta', 'announced'. 
Check homepage, product pages, announcements.
```

**Problem**: Too generic. Doesn't distinguish "announced plans" vs "actually launched"

### Perplexity (NO FALLBACK)
**Status is NOT in Perplexity fallback list**

### Test Results ✅
**Perplexity Query Tested**: `what is the current status of cheqd? Status could be as one of the below 4: 1. Announced: It has been publicly stated that the project is going to be developed. 2. Pilot: currently in testing stages before being publicly launched more widely. 3. Launched: it is already active and working. 4. Discontinued: project was declared discontinued publicly.`

**Result**: Perfect! Perplexity correctly returned "Launched" with detailed explanation.

**Action Taken**: ✅ Added Status field to Perplexity fallback in main.py

### Recommendations for Testing
1. ✅ **Test SerpAPI query**: `"cheqd" status launched pilot announced current state` (NEXT)
2. ✅ **Test Perplexity**: Already working!
3. **Improve LLM prompt**: Add more explicit guidance on distinguishing "announced plans" from "live product"

---

## 2. PROJECT ANNOUNCEMENT DATE Field

### Current State
- **Client**: "2019" (Trusted Biz)
- **AI**: "2021"
- **Issue**: AI using wrong year (likely project rebrand or feature announcement)

### Data Definition
```json
"data_definition": "The date the project was first publicly announced. If no official announcement was made, the date of the company's founding year will be used."
```

### SerpAPI Queries
**Current query**: `cheqd founded established launched announcement`
**Issue**: Too broad, mixes announcement with launch dates

### LLM Prompt
**Current prompt** (user_prompt_template):
```
For 'Announcement Date': Search for the FIRST time the project was publicly announced
Look for: 'founded in', 'established', 'announced', 'first introduced', company founding date
Check the OLDEST blog posts, press releases, or company history pages
```

**Field hint** (type_hints.year_announcement):
```
CRITICAL: Extract the FIRST/EARLIEST announcement year (YYYY format, 4 digits only). 
Search ALL pages for: 'founded in [YEAR]', 'established in [YEAR]', 'announced in [YEAR]', 'company history', 'our story', 'project announcement', 'first introduced'. 
Check the oldest blog posts, press releases, or company timeline pages. 
FILTER OUT: dates before 2000 (likely unrelated company history), dates after current year (future dates), copyright years, page last-updated dates. 
Use the original project/company founding/announcement date. 
If you see 'founded 2021' and 'announced features in 2023', use 2021. 
If no valid project-related date found (only unrelated old dates), use empty string.
```

**Problem**: Good guidance but AI still picking wrong dates

### Perplexity
**Current query**: `launch year for cheqd` (NOT announcement)
**Issue**: Wrong field - asking for "launch" not "announcement"

### Recommendations for Testing
1. **Test SerpAPI**: `"cheqd" announced first introduced founded when did it start`
2. **Test Perplexity**: `when was cheqd first announced or founded? give me the earliest year`
3. **Improve LLM prompt**: More emphasis on "ORIGINAL announcement, not recent features"

---

## 3. PROJECT LAUNCH DATE Field

### Current State
- **Trusted Biz**: Client "2019", AI "2021"
- **esatus**: Client "2020", AI "2015" or "N/A"
- **MÁS**: Client "2022", AI "2023"
- **Issue**: Inconsistent extraction, wrong years

### Data Definition
```json
"data_definition": "If applicable, the date the first deliverable/product went live.\nThe date can also refer to when the project first launched its beta version or a pilot version."
```

### SerpAPI Queries
**Current query**: `cheqd founded established launched announcement`
**Issue**: Same query as announcement date - too generic

### LLM Prompt
**Current prompt** (user_prompt_template):
```
For 'Launch Date': Search for when the product FIRST became available to users
Look for: 'launched', 'went live', 'beta release', 'first version', 'general availability (GA)', 'public release'
IMPORTANT: Do NOT use:
  * Recent blog post dates (these are when posts were published, not launch dates)
  * Feature announcement dates (these are when features were added, not the original launch)
  * Conference presentation dates
If you find multiple dates, use the EARLIEST one
```

**Field hint** (type_hints.year_launch):
```
CRITICAL: Extract the FIRST/EARLIEST launch year (YYYY format, 4 digits only). 
Look for when the product FIRST became available. 
Search for: 'launched in [YEAR]', 'went live in [YEAR]', 'beta release [YEAR]', 'first version [YEAR]', 'general availability [YEAR]', 'public launch [YEAR]', 'product launch'. 
FILTER OUT: dates before 2000 (likely unrelated), dates after current year (future dates - invalid), copyright years, blog post publication dates. 
DO NOT use recent launch dates for new features - use the original product launch. 
If you see 'launched 2021' and 'new features launched 2023', use 2021.
```

**Problem**: Good guidance but AI still confused between launch vs announcement

### Perplexity
**Current query**: `launch year for cheqd`
**Max tokens**: 50
**Problem**: Might return announcement date, not launch date

### Recommendations for Testing
1. **Test Perplexity**: `when did cheqd launch its product to users? first public beta or GA date`
2. **Test SerpAPI**: `"cheqd" product launch beta GA general availability went live when`
3. **Improve LLM**: Emphasize "FIRST public availability to users" vs "project announcement"

---

## 4. TECH STACK DESCRIPTIONS Field

### Current State
- **Client**: "We provide a vertical suite of solutions..."
- **AI**: "cheqd's tech stack includes a decentrali..."
- **Issue**: AI returning verbose paragraphs, not concise descriptions

### Data Definition
```json
"data_definition": "Self-description of the project regarding tech stack."
```

**Problem**: Too vague! No guidance on length or format

### SerpAPI Queries
**Current query**: `cheqd wallet ZKP zero-knowledge proofs`
**Issue**: Only finds tech mentions in articles, not official tech stack page

### LLM Prompt
**Field hint** (fallback_hints):
```
Tech Stack: Extract detailed technology descriptions from technical docs, developer pages, architecture docs.
```

**Field hint** (field_hints.tech_stack):
```
Extract detailed technology descriptions from technical documentation, developer pages, architecture docs, or product pages.
```

**Problem**: "Detailed" = too long. No guidance on keeping it concise

### Perplexity
**Current query**: `technology stack for cheqd`
**Max tokens**: 200
**Issue**: 200 tokens = too long for concise summary

### Recommendations for Testing
1. **Test Perplexity**: `give me a concise one-line tech stack description for cheqd, max 20 words`
2. **Test SerpAPI**: `site:cheqd.io technology architecture tech stack`
3. **Improve data definition**: Add length constraint like "Keep to 2-3 sentences max"

---

## 5. MISSION STATEMENT Field

### Current State
- **esatus**: Client "Enforcing Information Security", AI "Wir digitalisieren..." (German)
- **Trusted Biz**: Client "SSI4DTM is...", AI "JoinYourBit ha sviluppato..." (Italian)
- **Issue**: AI returning wrong language, paraphrasing instead of exact text

### Data Definition
```json
"data_definition": "Self-description of the project's goal/mission from an official source."
```

### SerpAPI Queries
**Current query**: `cheqd official website about mission`
**Good**: Uses "mission" keyword

### LLM Prompt
**Main instruction** (user_prompt_template):
```
Mission Statement: Extract the COMPLETE mission statement from official sources.
```

**Field hint** (field_hints.mission) - CURRENT:
```
CRITICAL: Extract the EXACT mission statement word-for-word in ENGLISH ONLY. 
The mission can be SHORT (1-5 words like 'Enforcing Information Security') or LONG (full sentences). 
Look for: sentences starting with action verbs ('To give', 'To enable', 'Our mission is', 'We aim'), SHORT taglines/headings describing purpose, or full paragraphs explaining WHY (not WHAT). 
PRIORITIZE: homepage hero/taglines, page titles/headers, 'About Us' first paragraph, 'Mission' pages. 
Extract EXACT English text - do NOT paraphrase, expand, TRANSLATE from other languages, or add context. 
If page is in another language, SKIP it and look for English version. 
If mission is short (e.g., 'Enforcing Information Security'), extract exactly that - do NOT extract a longer description. 
AVOID: product features, company histories, job descriptions, non-English text, marketing paragraphs. 
The mission is often in page headers, taglines, or the first sentence of About Us. 
CRITICAL: Return ONLY English text - if you see non-English content, find the English equivalent.
```

**Assessment**: Prompt is actually EXCELLENT but still failing on language detection

### Perplexity
**Current query**: `what is the mission statement of cheqd`
**Max tokens**: 300
**Issue**: Perplexity might return translated/paraphrased version, not original English

### Recommendations for Testing
1. **Test Perplexity**: `summarize the mission statement of cheqd in few lines` (as user suggested worked!)
2. **Test OpenAI**: Try the same mission statement prompt with gpt-4 to see if language issue is model-specific
3. **Verify Scraped Content**: Check if pages are being scraped in wrong language - might be scraping non-English pages

---

## 6. USES/ENDORSES ZKP Field

### Current State
- **cheqd**: Client "True", AI "Failed to disclose"
- **Issue**: AI not finding ZKP mentions even though it exists

### Data Definition
```json
"data_definition": "Defines whether the project uses ZKP or if it endorses it. In case there is no explicit information about the project actively using ZKP or having an opinion about it, \"Failed to disclose\" will be the response."
```

### SerpAPI Queries
**Current query**: `cheqd wallet ZKP zero-knowledge proofs`
**Good**: Uses "ZKP" keyword

### LLM Prompt
**Field hint** (field_hints.zkp_zero_knowledge):
```
CRITICAL: Look for mentions of zero-knowledge proofs (ZKP). 
Search for: 'zero-knowledge proofs', 'ZKP', 'zk-SNARKs', 'zk-STARKs', 'privacy-preserving proofs', 'anonymous credentials'. 
Check technical documentation, whitepapers, architecture docs, or feature pages. 
If project uses or endorses ZKP technology, use 'True'. 
If it does not use ZKP, use 'False'. 
If no information found about ZKP usage, use 'Failed to disclose'.
```

**Assessment**: Good guidance but AI not finding mentions

### Perplexity
**NOT in fallback list** - no Perplexity query

### Recommendations for Testing
1. **Test SerpAPI**: Verify if `cheqd wallet ZKP` actually returns relevant pages
2. **Test Perplexity**: `does cheqd use zero-knowledge proofs or ZKP technology?`
3. **Improve LLM**: Add more emphasis on searching technical docs (where ZKP is usually mentioned)

---

## SUMMARY OF TEST INPUTS TO TRY

### SerpAPI Queries to Test
1. **Status**: `"cheqd" status launched pilot announced current state`
2. **Announcement Date**: `"cheqd" announced first introduced founded when did it start`
3. **Launch Date**: `"cheqd" product launch beta GA general availability went live when`
4. **Tech Stack**: `site:cheqd.io technology architecture tech stack`
5. **Mission**: Already good: `cheqd official website about mission`
6. **ZKP**: Already good: `cheqd wallet ZKP zero-knowledge proofs`

### Perplexity Queries to Test
1. **Status**: `what is the current status of cheqd? is it launched or still in pilot?`
2. **Announcement Date**: `when was cheqd first announced or founded? give me the earliest year`
3. **Launch Date**: `when did cheqd launch its product to users? first public beta or GA date`
4. **Tech Stack**: `give me a concise one-line tech stack description for cheqd, max 20 words`
5. **Mission**: `summarize the mission statement of cheqd in few lines` (user's suggestion!)
6. **ZKP**: `does cheqd use zero-knowledge proofs or ZKP technology?`

### LLM Prompts to Refine
1. **Status**: Add explicit distinction between "announced plans" vs "live product"
2. **Dates**: More emphasis on ORIGINAL/earliest dates, not rebrand/feature dates
3. **Tech Stack**: Add length constraint "max 2-3 sentences"
4. **Mission**: Already good, but check if wrong language pages are being scraped
5. **ZKP**: Add emphasis on technical documentation sources

---

## NEXT STEPS
Test these queries manually in:
1. **Perplexity**: Try the 6 Perplexity queries above
2. **SerpAPI**: Check if refined queries return better results
3. **OpenAI Chat**: Copy the current LLM prompts and test with sample pages

Then we'll refine the prompts based on results!

