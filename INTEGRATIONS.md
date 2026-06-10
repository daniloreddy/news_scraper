# Integration Guide: news-scraper with n8n

This guide explains how to connect and orchestrate the `news-scraper` microservice with **n8n**, including publishing to **WordPress** and sending alerts to **Telegram**.

## Complete Workflow

```
[Cron] → [HTTP Request /scrape] → [Code: Extract first article]
       → [LLM: Italian Summary] → [Split in two branches]
             ├── [WordPress: Create draft]
             └── [Telegram: Send message]
```

## Node: HTTP Request

- **Method**: POST
- **URL**: `https://your.end.point/scrape`
- **Body (JSON)**:
  ```json
  { "max_articles": 1 }
  ```
- **Response**: JSON array

## Node: Code (Extract Data)

```javascript
// Input: Array response from /scrape
const articles = $input.first().json;
const art = Array.isArray(articles) ? articles[0] : articles;

return [{
  json: {
    title: art.title,
    url: art.url,
    date: art.published_date,
    content: art.content,
    thumbnail: art.thumbnail_url
  }
}];
```

## Node: LLM (Summary)

**Prompt:**

```
You are a helpful assistant. Summarize the following news article in Italian.

The summary must:
- Be written in clear, engaging Italian suitable for a gaming blog
- Be 150-200 words
- Include the most important game mechanics, events, or features mentioned
- NOT include phrases like "In questo articolo" or "L'articolo descrive"
- End with a call to action (e.g., "Consultate la notizia completa per tutti i dettagli!")

Article title: {{ $json.title }}
Article content:
{{ $json.content }}

Respond with ONLY the Italian summary, no preamble.
```

## Node: WordPress (Create Draft)

- **Credential**: WordPress (Application Password)
- **Resource**: Post
- **Operation**: Create
- **Title**: `🔥 {{ $json.title }}`
- **Content**:
  ```
  {{ $json.summary }}

  <hr>
  <p><strong>Fonte originale:</strong> <a href="{{ $json.url }}" target="_blank">{{ $json.url }}</a></p>
  <p><em>Data pubblicazione: {{ $json.date }}</em></p>
  ```
- **Status**: `draft` (set to `publish` once confident)

### Configure WordPress Application Password

1. WP Admin → Users → Your Profile
2. Scroll down to "Application Passwords"
3. Name: `n8n-scraper` → click "Add New Application Password"
4. Copy the generated password (only visible once)
5. In n8n: Add WordPress credentials with your username and the application password

## Node: Telegram

- **Credential**: Telegram API (existing bot credentials)
- **Resource**: Message
- **Operation**: Send Message
- **Chat ID**: Your Telegram group/chat ID
- **Text**:
  ```
  🎮 *New Announcement!*

  *{{ $json.title }}*
  _{{ $json.date }}_

  {{ $json.summary }}

  🔗 [Read full news]({{ $json.url }})
  ```
- **Parse Mode**: Markdown

---

## Avoiding Duplicates

To avoid sending the same announcement twice, add a **Code** node before the LLM to verify if the URL has already been processed. You can use:

- A local JSON file read/written by the Code node
- n8n Static Workflow Data
- A Google Sheets / Airtable spreadsheet row

**Example with n8n Static Data:**

```javascript
// At the beginning of the workflow (after HTTP Request)
const seen = $getWorkflowStaticData('global');
if (!seen.processed) seen.processed = [];

const url = $input.first().json[0].url;

if (seen.processed.includes(url)) {
  // News already sent, stop workflow
  return [];   // Empty array terminates the workflow execution
}

// Otherwise append and continue
seen.processed.push(url);
// Keep only the last 50 processed URLs
if (seen.processed.length > 50) seen.processed.shift();

return $input.all();
```
