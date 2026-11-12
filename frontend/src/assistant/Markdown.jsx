// Minimal, safe markdown for AI text: paragraphs, "- " bullets and **bold**.
// Builds React elements (no innerHTML), so model output can't inject markup.

function inline(text, keyPrefix) {
  return text.split(/(\*\*[^*]+\*\*)/g).map((part, i) =>
    part.startsWith("**") && part.endsWith("**")
      ? <strong key={`${keyPrefix}-${i}`}>{part.slice(2, -2)}</strong>
      : part
  );
}

export default function Markdown({ text }) {
  const blocks = [];
  let list = null;
  (text || "").split("\n").forEach((raw, i) => {
    const line = raw.trim();
    const bullet = line.match(/^[-*•]\s+(.*)/);
    if (bullet) {
      list = list || [];
      list.push(<li key={i}>{inline(bullet[1], i)}</li>);
      return;
    }
    if (list) {
      blocks.push(<ul key={`ul-${i}`}>{list}</ul>);
      list = null;
    }
    if (line) blocks.push(<p key={i}>{inline(line.replace(/^#+\s*/, ""), i)}</p>);
  });
  if (list) blocks.push(<ul key="ul-end">{list}</ul>);
  return <div className="md">{blocks}</div>;
}
