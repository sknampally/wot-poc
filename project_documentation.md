# Web of Trust Data Collection System
## Client Documentation

**Version:** 1.0  
**Last Updated:** 2025  
**Document Type:** Technical Documentation

---

## Table of Contents

1. [Executive Summary](#executive-summary)
2. [System Overview](#system-overview)
3. [Key Features](#key-features)
4. [Architecture & Data Flow](#architecture--data-flow)
5. [Data Schema](#data-schema)
6. [Quality Metrics](#quality-metrics)
7. [Configuration](#configuration)
8. [Output Format](#output-format)
9. [API Integrations](#api-integrations)
10. [Appendices](#appendices)

---

## Executive Summary

The **Web of Trust (WOT) Data Collection System** is an AI-powered automated platform that extracts structured information about Digital Identity and Self-Sovereign Identity (SSI) projects from publicly available web sources.

### What It Does

The system automates the complete data collection workflow:

1. **Searches** the internet for relevant project information
2. **Scrapes** web pages to extract content
3. **Extracts** structured data using AI (LLMs)
4. **Validates** extracted data against manual/client data
5. **Exports** results to Excel with source attribution

### Key Benefits

✅ **Automated**: Reduces manual research time by 80-90%  
✅ **Attributed**: Every value includes source URL for verification  
✅ **Validated**: Built-in accuracy and coverage metrics  
✅ **Scalable**: Processes multiple projects simultaneously  
✅ **Flexible**: Configurable extraction rules  
✅ **Transparent**: Complete audit trail

---

## System Overview

### Purpose

The system is designed to populate a comprehensive database of Digital Identity projects with structured information extracted from:
- Official project websites
- Documentation pages
- Blog posts and announcements
- Press releases
- Technical documentation
- GitHub repositories

### Core Components

1. **Search Engine**: Uses external API to find relevant URLs via Google Search
2. **Web Scraper**: Extracts clean text content from HTML pages
3. **LLM Extractor**: Uses OpenAI GPT-4 or local Ollama models to extract structured data
4. **Perplexity Fallback**: Uses Perplexity AI for web-grounded extraction when primary extraction fails
5. **Data Validator**: Compares AI results against manual data and calculates quality metrics
6. **Excel Exporter**: Generates structured output with source tracking

### Workflow

```
Input (Project Names) 
    ↓
[1] Search Phase → Find relevant URLs
    ↓
[2] Scraping Phase → Extract text from web pages
    ↓
[3] Extraction Phase → LLM extracts structured data
    ↓
[4] Fallback Phase → Perplexity fills missing fields
    ↓
[5] Validation Phase → Compare with manual data
    ↓
[6] Export Phase → Generate Excel output
    ↓
Output (Structured Data + Quality Metrics)
```

---

## Key Features

### 1. Intelligent Web Search

- **Multi-query Strategy**: Performs targeted searches (e.g., "project name SSI", "project name about")
- **Blurb Context**: Uses project blurbs from `manual_seeds.json` to improve search query accuracy
- **Source Prioritization**: Prioritizes official sources (homepage > about > docs > blog)
- **Known Website Seeding**: Uses known websites from `manual_seeds.json` to improve search accuracy
- **Domain Filtering**: Excludes irrelevant domains (YouTube, social media, etc.)

### 2. LLM-Powered Extraction

- **Structured Extraction**: Extracts 40+ fields per project
- **Field-Specific Guidance**: Custom extraction rules per field type
- **Evidence Tracking**: Links each extracted value to its source URL
- **Confidence Scoring**: High/Medium/Low confidence levels per extraction

### 3. Perplexity AI Fallback

- **Automatic Fallback**: Triggers when primary extraction returns empty or "Failed to disclose"
- **Web-Grounded Search**: Uses real-time web search for missing information
- **Disambiguation**: Adds context to queries to avoid confusion with similarly named entities
- **Cost-Effective**: Only queries fields that need additional information

### 4. Quality Assurance

- **Accuracy Metrics**: Compares AI results against manual data
- **Coverage Metrics**: Calculates percentage of filled fields
- **Semantic Matching**: Uses similarity algorithms for text field comparison
- **URL Normalization**: Compares URLs by domain (ignores paths, trailing slashes)

### 5. Flexible Configuration

- **Codebook-Driven**: Field definitions stored in Excel/JSON
- **Prompt Engineering**: Extraction prompts in JSON (no code changes needed)
- **Configurable LLM**: Supports OpenAI, Ollama (local), and Perplexity
- **Customizable Thresholds**: Adjustable similarity thresholds for matching

### 6. Audit Trail

- **Source Attribution**: Every field includes source URL
- **Evidence Logging**: Complete extraction evidence stored in JSON
- **Cache System**: All scraped content cached for debugging
- **Execution Logs**: Detailed logs of all operations

---

## Architecture & Data Flow

### System Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Main Application (main.py)               │
└─────────────────────────────────────────────────────────────┘
                            │
        ┌───────────────────┼───────────────────┐
        │                   │                   │
        ▼                   ▼                   ▼
┌──────────────┐   ┌──────────────┐   ┌──────────────┐
│   Searcher   │   │   Scraper    │   │  Extractor   │
│ (SerpAPI)    │──▶│ (Beautiful   │──▶│ (LLM Client) │
│              │   │   Soup)      │   │              │
└──────────────┘   └──────────────┘   └──────────────┘
                            │                   │
                            │                   ▼
                            │          ┌──────────────┐
                            │          │  Perplexity  │
                            │          │   Fallback   │
                            │          └──────────────┘
                            │                   │
                            ▼                   ▼
                    ┌──────────────┐   ┌──────────────┐
                    │   Cache      │   │   Exporter   │
                    │  (JSON/TXT)  │   │   (Excel)    │
                    └──────────────┘   └──────────────┘
```

### Data Flow Details

#### Phase 1: Search (Searcher)
- **Input**: Project name, known website (from `manual_seeds.json`), blurb (from `manual_seeds.json`)
- **Process**: 
  - Loads known website and blurb from `data/manual_seeds.json`
  - Constructs search queries using blurb context (e.g., "cheqd decentralized identity network...")
  - Uses `site:` queries when known website is provided
  - Calls SerpAPI to get Google Search results
  - Filters and prioritizes URLs (homepage > about > docs)
  - Uses known website to seed search results and generate common pages
- **Output**: List of prioritized URLs (max 50 per project)

#### Phase 2: Scraping (Scraper)
- **Input**: List of URLs
- **Process**:
  - Fetches HTML from each URL
  - Extracts clean text using BeautifulSoup
  - Filters out navigation, footers, ads
  - Caches scraped content for debugging
- **Output**: Structured text content per URL

#### Phase 3: Extraction (Extractor)
- **Input**: Scraped text from multiple pages
- **Process**:
  - Prioritizes pages (homepage > about > docs > blog)
  - Packs top 15 pages with 8K chars each into context
  - Builds LLM prompt with field-specific guidance
  - Calls OpenAI/Ollama to extract structured JSON
  - Post-processes results (normalization, validation)
- **Output**: Structured data record with evidence

#### Phase 4: Perplexity Fallback
- **Input**: Fields with empty or "Failed to disclose" values
- **Process**:
  - Checks which fields have Perplexity configuration
  - Constructs disambiguated queries (e.g., "cheqd digital identity project announcement date")
  - Calls Perplexity API for web-grounded search
  - Post-processes responses (extracts lists, normalizes dates)
- **Output**: Filled missing fields with source URLs

#### Phase 5: Validation (Accuracy Checker)
- **Input**: AI-extracted data, manual/client data
- **Process**:
  - Compares each field using appropriate matching logic
  - Uses semantic similarity for long text fields
  - Normalizes URLs, entity names, dates
  - Calculates accuracy and coverage metrics
- **Output**: Accuracy report with field-level comparison

#### Phase 6: Export (Excel Exporter)
- **Input**: AI data, manual data, comparison results
- **Process**:
  - Creates three Excel sheets:
    - **Input**: Original manual/client data
    - **AI**: AI-extracted data with source URLs
    - **Comparison**: Side-by-side comparison
  - Formats data with proper types
  - Adds source columns for each field
- **Output**: `output.xlsx` file

---

## Data Schema

### Field Categories

The system extracts **40+ fields** across the following categories:

#### 1. Basic Information
- **Product Name**: Project/product name
- **Website**: Official website URL
- **Logo**: Logo image URL
- **Mission Statement**: Complete mission statement
- **Status**: Project lifecycle stage (Announced, Pilot, Launched, Discontinued)

#### 2. Dates & Timeline
- **Project Announcement Date**: Year when project was first announced
- **Project Launch Date**: Year when product first became available

#### 3. Organizational Information
- **Managing Entity**: Organization managing the project
- **Affiliated Entity / Partner**: Partner organizations
- **Private Sector Funding**: Funding information

#### 4. Technical Information
- **Tech Stack Descriptions**: Technology stack details
- **Public Code Repository**: GitHub/GitLab repository URL
- **Blockchains / Verifiable Data Registries**: Blockchain platforms used
- **Standard/Protocol Used**: Standards and protocols (comma-separated)
- **Regulations Followed**: Regulatory compliance (comma-separated)

#### 5. Capabilities & Features
- **Uses/endorses ZKP**: Zero-knowledge proof support (True/False/Failed to disclose)
- **Has Exportable Credentials**: Credential export capability
- **Credential And Key Storage**: Storage mechanism (True/False/Failed to disclose)

#### 6. Target Users
- **Targets Holders**: Supports credential holders (True/False/Failed to disclose)
- **Targets Issuers**: Supports credential issuers (True/False/Failed to disclose)
- **Targets Verifiers**: Supports credential verifiers (True/False/Failed to disclose)

#### 7. Use Cases & Applications
- **Use Cases**: Primary use cases and applications
- **Politically Involved?**: Government/public sector involvement

#### 8. Distribution
- **App Store Link**: Mobile app store URL

### Field Types

| Type | Description | Example |
|------|-------------|---------|
| **Text** | Free-form text | Mission Statement |
| **URL** | Web URL | Website, Logo, Code Repository |
| **Year** | 4-digit year (YYYY) | Project Launch Date |
| **Status** | Enum: Announced, Pilot, Launched, Discontinued | Status |
| **Ternary** | True, False, or Failed to disclose | Uses/endorses ZKP |
| **List** | Comma-separated values | Standard/Protocol Used |

### Source Attribution

Every extracted field includes:
- **Source URL**: Where the information was found
- **Source Type**: Type of source (webpage, documentation, etc.)
- **Confidence**: High, Medium, or Low

Stored in:
- **Live Source [Field Name]**: Current source URL for each field
- **Archived Source [Field Name]**: Archived source URL (if applicable)

---

## Quality Metrics

### Accuracy Metrics

**Definition**: Percentage of AI-extracted fields that match manual/client-provided values.

**Calculation**:
```
Accuracy = (Matching Fields / Total Fields) × 100
```

**Included Fields**:
- ✅ All data fields from codebook where `extraction_needed=Y`
- ✅ Only projects with manual data available

**Excluded Fields**:
- ❌ Product Name, ID, Logo (reference fields)
- ❌ Source columns (Live Source, Archived Source)
- ❌ Internal metadata (`_evidence`)

**Matching Logic**:
- **Exact Match**: For URLs, dates, booleans
- **Semantic Similarity**: For long text fields (60% threshold)
- **Domain Match**: For URLs (ignores paths, trailing slashes)
- **Normalized Match**: For entity names (handles parenthetical names)
- **List Match**: For comma-separated lists (checks if manual value appears in AI list)

**Current Performance**:
- **Overall Accuracy**: 52.4% (65/124 fields across all projects)
- **Project-Specific**: Ranges from 45% to 71% depending on project

### Coverage Metrics

**Definition**: Percentage of fields that are filled with actual data (non-empty values).

**Calculation**:
```
Coverage = (Filled Fields / Total Fields) × 100
```

**Field Status**:
- **Filled**: Has non-empty value
- **Empty**: No value found
- **Failed to Disclose**: Valid response meaning information was not found (counted as empty)

**Included Fields**:
- ✅ All data fields from codebook where `extraction_needed=Y`

**Excluded Fields**:
- ❌ Product Name, ID, Logo
- ❌ Source columns
- ❌ Internal metadata

**Coverage Thresholds**:
- ✅ **≥80%**: Excellent coverage
- ⚠️ **60-79%**: Good coverage, some fields need attention
- ❌ **<60%**: Low coverage, consider improving extraction strategies

**Current Performance**:
- **Average Coverage**: 45-65% (varies by project)
- **High Coverage Projects**: 65-75%
- **Low Coverage Projects**: 30-45%

### Quality Report

The system generates detailed quality reports showing:
- **Project-level Metrics**: Accuracy and coverage per project
- **Field-level Comparison**: Side-by-side comparison of manual vs AI values
- **Mismatch Analysis**: Which fields don't match and why
- **Source Attribution**: Source URLs for all extracted values

---

> **Note**: For setup instructions, usage guide, and command-line options, please refer to [README.md](README.md).

## Configuration

> **Note**: For manual seeds configuration, please refer to [README.md](README.md) Section 4.

### Codebook Configuration

The **codebook** defines field definitions, extraction rules, and validation constraints.

**Location**: `data/codebook.json` (auto-generated from `data/wot_data_definations.xlsx`)

**Import Codebook**:
```bash
python src/main.py --import-codebook data/wot_data_definations.xlsx
```

**Codebook Structure**:
- **Field Definitions**: Field name, type, extraction rules
- **Status Enums**: Valid status values
- **Ternary Enums**: True/False/Failed to disclose values
- **Normalization Rules**: How to normalize extracted values

### Prompt Configuration

All LLM extraction prompts are stored in `data/prompts.json` for easy editing without code changes.

**Key Sections**:
- **system_prompt**: High-level LLM instructions
- **user_prompt_template**: Main extraction template
- **field_hints**: Field-specific extraction guidance
- **fields**: Per-field Perplexity query templates

**Example**: To improve mission statement extraction, edit the `"mission"` key in `field_hints`.

**Changes take effect immediately** on next run - no code redeployment needed.

> **Note**: For environment configuration (.env file), please refer to [README.md](README.md) Section 3.

---

## Output Format

### Excel Output (`data/output.xlsx`)

#### Sheet 1: Input
- Original manual/client-provided data
- All columns from `input.xlsx`

#### Sheet 2: AI
- AI-extracted data with source attribution
- Columns:
  - All data fields (Product Name, Website, Mission Statement, etc.)
  - **Source [Field Name]**: Source URL for each field
  - **Archived Source [Field Name]**: Archived source URL (if applicable)

#### Sheet 3: Comparison
- Side-by-side comparison of manual vs AI data
- Only includes projects with manual data
- Columns:
  - **Product Name**: Project identifier
  - **Field Name**: Name of field being compared
  - **Manual Value**: Client-provided value
  - **AI Value**: AI-extracted value
  - **Match?**: Yes/No/Partial match indicator

### Cache Files

All intermediate data is cached for debugging. See [README.md](README.md) Section 7 for details on cache file structure.

### Log Files

**Location**: `logs/wot.log`

**Log Levels**:
- **INFO**: General execution flow
- **DEBUG**: Detailed extraction steps
- **ERROR**: Errors and exceptions

---

## API Integrations

### SerpAPI Integration

**Purpose**: Google Search results without scraping

**Usage**:
- Searches for project-related URLs
- Filters results by relevance
- Prioritizes official sources

**Configuration**:
- API key in `.env`: `SERPAPI_API_KEY`
- Max URLs per project: `MAX_URLS_PER_PROJECT` (default: 50)

### OpenAI Integration

**Purpose**: Primary LLM for structured data extraction

**Configuration**:
- API key in `.env`: `OPENAI_API_KEY`
- Provider: `LLM_PROVIDER=openai`
- Model: `LLM_MODEL=gpt-4o-mini`
- Max tokens: `LLM_MAX_TOKENS=4000`

### Perplexity AI Integration

**Purpose**: Fallback extraction with real-time web search

**Usage**:
- Automatically triggered for empty or "Failed to disclose" fields
- Queries configured in `data/prompts.json`
- Disambiguates queries with context (e.g., "digital identity project")

**Configuration**:
- API key in `.env`: `PERPLEXITY_API_KEY`
- Query templates in `data/prompts.json`

### Ollama Integration (Optional)

**Purpose**: Local LLM for offline processing

**Setup**:
```bash
# Install Ollama
curl -fsSL https://ollama.ai/install.sh | sh

# Download model
ollama pull llama3.1

# Start server
ollama serve
```

**Configuration**:
- Provider: `LLM_PROVIDER=ollama`
- Model: `OLLAMA_MODEL=llama3.1`
- Host: `OLLAMA_HOST=http://localhost:11434`

**Note**: Local models may have lower extraction quality compared to OpenAI.

> **Note**: For best practices, troubleshooting, and getting help, please refer to [README.md](README.md) Sections 11 and 12.

---

## Appendices

### Appendix A: Field Definitions Reference

See `data/wot_data_definations.xlsx` for complete field definitions.

### Appendix B: Codebook Schema

See `data/codebook.json` for complete codebook structure.

### Appendix C: Prompt Templates

See `data/prompts.json` for all LLM extraction prompts.

### Appendix D: Accuracy Calculation Details

See `src/app/utils/accuracy.py` for matching logic implementation.

---

## Document History

| Version | Date | Changes |
|---------|------|---------|
| 1.0 | 2025 | Initial client documentation |
| | | Added Perplexity fallback documentation |
| | | Added accuracy and coverage metrics |
| | | Added configuration guide |

---

**For technical support or questions, please contact the development team.**

