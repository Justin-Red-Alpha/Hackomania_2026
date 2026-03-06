import { useState } from "react";

const inputFields = [
  { name: "articleUrl", type: "string (URI)", required: true, description: "URL of the news article to check" },
  { name: "anthropicApiKey", type: "string (secret)", required: true, description: "Claude API key" },
  { name: "tavilyApiKey", type: "string (secret)", required: true, description: "Tavily API key for search + extraction" },
  { name: "tavilySearchDepth", type: "enum", required: false, default: "advanced", description: "basic = faster, advanced = thorough" },
  { name: "tavilyMaxResults", type: "integer", required: false, default: "5", description: "Max search results per claim" },
  { name: "prioritiseLocalSources", type: "boolean", required: false, default: "false", description: "Prioritise country-specific sources" },
  { name: "country", type: "string", required: false, description: "e.g. Singapore, USA" },
  { name: "minimumSourcesPerClaim", type: "integer", required: false, default: "2", description: "Min independent sources per claim" },
  { name: "excludeGovernmentSourcesOnly", type: "boolean", required: false, default: "true", description: "Flag govt-only verdicts" },
];

const processingSteps = [
  { icon: "⬇", tool: "tavily-extract", label: "Scrape & clean article text", color: "#06b6d4" },
  { icon: "⬇", tool: "Claude", label: "Read article & identify claims", color: "#a78bfa" },
  { icon: "⬇", tool: "tavily-search", label: "Search for sources per claim", color: "#06b6d4" },
  { icon: "⬇", tool: "tavily-extract", label: "Pull full content from source pages", color: "#06b6d4" },
  { icon: "⬇", tool: "Claude", label: "Analyse, score & generate output", color: "#a78bfa" },
];

const outputSections = [
  {
    label: "article",
    color: "#8b5cf6",
    description: "Article Metadata",
    note: "Extracted by tavily-extract",
    fields: [
      { name: "url", type: "string" },
      { name: "title", type: "string" },
      { name: "publisher", type: "string" },
      { name: "date", type: "date" },
      { name: "author", type: "string" },
      { name: "section", type: "string" },
      { name: "is_opinion", type: "boolean" },
    ]
  },
  {
    label: "publisher_credibility",
    color: "#f59e0b",
    description: "Publisher Credibility Score",
    note: "Generated after check ✦",
    fields: [
      { name: "score", type: "integer 0–100" },
      { name: "rating", type: "enum", note: "highly_credible → not_credible" },
      { name: "summary", type: "string" },
      { name: "bias", type: "enum", note: "far_left → far_right" },
      { name: "known_issues[]", type: "array" },
      { name: "fact_checker_ratings[]", type: "array" },
    ]
  },
  {
    label: "article_credibility",
    color: "#10b981",
    description: "Article Accuracy Score",
    note: "Generated after check ✦",
    fields: [
      { name: "score", type: "integer 0–100" },
      { name: "rating", type: "enum", note: "credible → false" },
      { name: "summary", type: "string" },
      { name: "total_claims_found", type: "integer" },
      { name: "claims_true / false / etc", type: "integer" },
      { name: "government_source_only_flag", type: "boolean" },
      { name: "writing_quality{}", type: "object", note: "sensationalism, named sources..." },
    ]
  },
  {
    label: "claims[]",
    color: "#ef4444",
    description: "Individual Claim Breakdown",
    note: "Generated after check ✦",
    fields: [
      { name: "claim_id", type: "integer" },
      { name: "claim_summary", type: "string" },
      { name: "extract", type: "string", note: "Direct quote" },
      { name: "verdict", type: "enum", note: "true → false" },
      { name: "reason", type: "string" },
      { name: "government_source_only", type: "boolean" },
      { name: "sources[]", type: "array", note: "name, url, type, is_independent" },
    ]
  }
];

function Field({ name, type, required, default: def, description, note }) {
  return (
    <div style={{
      display: "flex",
      alignItems: "flex-start",
      gap: "10px",
      padding: "8px 16px",
      borderBottom: "1px solid rgba(255,255,255,0.05)",
      fontSize: "13.5px",
      fontFamily: "'JetBrains Mono', 'Fira Code', monospace",
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: "8px", minWidth: "230px", flexShrink: 0 }}>
        {required !== undefined && (
          <span style={{
            fontSize: "10px",
            padding: "2px 5px",
            borderRadius: "3px",
            background: required ? "#ef444420" : "#ffffff10",
            color: required ? "#ef4444" : "#555",
            fontWeight: 700,
            flexShrink: 0
          }}>
            {required ? "REQ" : "OPT"}
          </span>
        )}
        <span style={{ color: "#e2e8f0", fontWeight: 500 }}>{name}</span>
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: "3px" }}>
        <span style={{ color: "#94a3b8", fontSize: "12px" }}>{type}</span>
        {note && <span style={{ color: "#64748b", fontSize: "11px", fontStyle: "italic" }}>{note}</span>}
        {def !== undefined && <span style={{ color: "#64748b", fontSize: "11px" }}>default: {def}</span>}
        {description && <span style={{ color: "#475569", fontSize: "11px" }}>{description}</span>}
      </div>
    </div>
  );
}

function SchemaBox({ label, color, description, note, fields }) {
  const [open, setOpen] = useState(true);
  return (
    <div style={{
      border: `1.5px solid ${color}40`,
      borderRadius: "10px",
      overflow: "hidden",
      background: "#0a1628",
      boxShadow: `0 0 20px ${color}12`,
      marginBottom: "10px"
    }}>
      <div
        onClick={() => setOpen(!open)}
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "10px 14px",
          background: `${color}15`,
          borderBottom: open ? `1px solid ${color}25` : "none",
          cursor: "pointer",
          userSelect: "none"
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: "10px", flexWrap: "wrap" }}>
          <div style={{ width: "10px", height: "10px", borderRadius: "50%", background: color, flexShrink: 0 }} />
          <span style={{ fontFamily: "'JetBrains Mono', monospace", color, fontWeight: 700, fontSize: "15px" }}>
            {label}
          </span>
          {description && (
            <span style={{ color: "#64748b", fontSize: "13px", fontFamily: "sans-serif" }}>— {description}</span>
          )}
          {note && (
            <span style={{
              fontSize: "11px",
              padding: "2px 8px",
              borderRadius: "10px",
              background: `${color}20`,
              color,
              fontFamily: "monospace",
              letterSpacing: "0.03em"
            }}>{note}</span>
          )}
        </div>
        <span style={{ color: "#475569", fontSize: "12px" }}>{open ? "▲" : "▼"}</span>
      </div>
      {open && fields && fields.map((f, i) => <Field key={i} {...f} />)}
    </div>
  );
}

function ProcessingPipeline() {
  return (
    <div style={{
      margin: "0",
      padding: "16px",
      background: "#0a1628",
      border: "1.5px solid #1e3a5f",
      borderRadius: "10px",
      boxShadow: "0 0 30px #06b6d410",
    }}>
      <div style={{
        display: "flex",
        alignItems: "center",
        gap: "8px",
        marginBottom: "14px"
      }}>
        <div style={{ width: "10px", height: "10px", borderRadius: "50%", background: "#06b6d4" }} />
        <span style={{ color: "#06b6d4", fontFamily: "monospace", fontWeight: 700, fontSize: "14px" }}>
          ACTOR PROCESSING
        </span>
        <span style={{ color: "#1e3a5f", fontSize: "12px" }}>— powered by Tavily MCP + Claude</span>
      </div>

      {processingSteps.map((step, i) => (
        <div key={i}>
          <div style={{
            display: "flex",
            alignItems: "center",
            gap: "10px",
            padding: "8px 10px",
            borderRadius: "7px",
            background: i % 2 === 0 ? "#06b6d410" : "#a78bfa10",
            border: `1px solid ${step.color}20`,
            marginBottom: "4px"
          }}>
            <span style={{
              fontSize: "12px",
              padding: "3px 10px",
              borderRadius: "5px",
              background: `${step.color}20`,
              color: step.color,
              fontFamily: "monospace",
              fontWeight: 700,
              flexShrink: 0,
              minWidth: "125px",
              textAlign: "center"
            }}>{step.tool}</span>
            <span style={{ color: "#94a3b8", fontSize: "13px" }}>{step.label}</span>
          </div>
          {i < processingSteps.length - 1 && (
            <div style={{
              display: "flex",
              justifyContent: "center",
              margin: "2px 0"
            }}>
              <div style={{
                display: "flex",
                flexDirection: "column",
                alignItems: "center",
                gap: "1px"
              }}>
                <div style={{ width: "1px", height: "8px", background: "#1e3a5f" }} />
                <div style={{ width: 0, height: 0, borderLeft: "4px solid transparent", borderRight: "4px solid transparent", borderTop: "5px solid #1e3a5f" }} />
              </div>
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

function Divider({ label, color }) {
  return (
    <div style={{
      display: "flex",
      alignItems: "center",
      gap: "10px",
      margin: "16px 0 10px"
    }}>
      <div style={{ height: "1px", flex: 1, background: "#1e293b" }} />
      <span style={{ color, fontSize: "12px", fontFamily: "'JetBrains Mono', monospace", letterSpacing: "0.12em" }}>{label}</span>
      <div style={{ height: "1px", flex: 1, background: "#1e293b" }} />
    </div>
  );
}

function Arrow() {
  return (
    <div style={{ display: "flex", justifyContent: "center", margin: "12px 0" }}>
      <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: "2px" }}>
        <div style={{ width: "1px", height: "16px", background: "#1e3a5f" }} />
        <div style={{ width: 0, height: 0, borderLeft: "6px solid transparent", borderRight: "6px solid transparent", borderTop: "8px solid #1e3a5f" }} />
      </div>
    </div>
  );
}

export default function SchemaDiagram() {
  return (
    <div style={{
      background: "#060d1a",
      minHeight: "100vh",
      padding: "32px 24px",
      fontFamily: "system-ui, sans-serif",
      backgroundImage: `
        radial-gradient(circle at 15% 15%, #06b6d408 0%, transparent 45%),
        radial-gradient(circle at 85% 85%, #8b5cf608 0%, transparent 45%)
      `
    }}>
      {/* Header */}
      <div style={{ textAlign: "center", marginBottom: "28px" }}>
        <div style={{ display: "flex", justifyContent: "center", gap: "8px", marginBottom: "12px", flexWrap: "wrap" }}>
          <span style={{
            padding: "3px 12px",
            background: "#06b6d415",
            border: "1px solid #06b6d430",
            borderRadius: "20px",
            color: "#06b6d4",
            fontSize: "10px",
            fontFamily: "monospace",
            letterSpacing: "0.1em"
          }}>TAVILY MCP</span>
          <span style={{
            padding: "4px 14px",
            background: "#a78bfa15",
            border: "1px solid #a78bfa30",
            borderRadius: "20px",
            color: "#a78bfa",
            fontSize: "12px",
            fontFamily: "monospace",
            letterSpacing: "0.1em"
          }}>CLAUDE</span>
          <span style={{
            padding: "4px 14px",
            background: "#f59e0b15",
            border: "1px solid #f59e0b30",
            borderRadius: "20px",
            color: "#f59e0b",
            fontSize: "12px",
            fontFamily: "monospace",
            letterSpacing: "0.1em"
          }}>APIFY ACTOR</span>
        </div>
        <h1 style={{
          color: "#f1f5f9",
          fontSize: "26px",
          fontWeight: 700,
          margin: "0 0 8px",
          letterSpacing: "-0.02em"
        }}>Article Credibility Checker</h1>
        <p style={{ color: "#334155", fontSize: "13px", margin: 0, fontFamily: "monospace" }}>Schema Diagram — Input → Processing → Output</p>
      </div>

      <div style={{ maxWidth: "820px", margin: "0 auto" }}>

        {/* INPUT */}
        <Divider label="INPUT" color="#0ea5e9" />
        <SchemaBox
          label="Input"
          color="#0ea5e9"
          description="Actor configuration"
          fields={inputFields}
        />

        <Arrow />

        {/* PROCESSING */}
        <Divider label="PROCESSING" color="#06b6d4" />
        <ProcessingPipeline />

        <Arrow />

        {/* OUTPUT */}
        <Divider label="OUTPUT — only available after check completes" color="#8b5cf6" />
        {outputSections.map((section, i) => (
          <SchemaBox key={i} {...section} />
        ))}

        {/* Legend */}
        <div style={{
          marginTop: "20px",
          padding: "14px 16px",
          background: "#0a1628",
          border: "1px solid #1e293b",
          borderRadius: "10px",
        }}>
          <div style={{ color: "#334155", fontSize: "12px", fontFamily: "monospace", marginBottom: "10px", letterSpacing: "0.08em" }}>LEGEND</div>
          <div style={{ display: "flex", gap: "16px", flexWrap: "wrap" }}>
            {[
              { label: "Required", color: "#ef4444", tag: "REQ" },
              { label: "Optional", color: "#555", tag: "OPT" },
              { label: "tavily-extract / tavily-search", color: "#06b6d4", dot: true },
              { label: "Claude analysis", color: "#a78bfa", dot: true },
              { label: "Article metadata", color: "#8b5cf6", dot: true },
              { label: "Publisher score", color: "#f59e0b", dot: true },
              { label: "Article score", color: "#10b981", dot: true },
              { label: "Claims array", color: "#ef4444", dot: true },
              { label: "Post-check only ✦", color: "#f59e0b", dot: false, italic: true },
            ].map((item, i) => (
              <div key={i} style={{ display: "flex", alignItems: "center", gap: "7px", fontSize: "12px", color: "#64748b", fontStyle: item.italic ? "italic" : "normal" }}>
                {item.tag ? (
                  <span style={{
                    padding: "2px 6px",
                    borderRadius: "3px",
                    background: `${item.color}20`,
                    color: item.color,
                    fontFamily: "monospace",
                    fontSize: "10px",
                    fontWeight: 700
                  }}>{item.tag}</span>
                ) : (
                  <div style={{ width: "9px", height: "9px", borderRadius: "50%", background: item.color, flexShrink: 0 }} />
                )}
                {item.label}
              </div>
            ))}
          </div>
        </div>

        <p style={{ textAlign: "center", color: "#1e293b", fontSize: "10px", marginTop: "16px", fontFamily: "monospace" }}>
          Click any section header to collapse / expand
        </p>
      </div>
    </div>
  );
}
