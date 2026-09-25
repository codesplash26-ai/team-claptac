import { useState } from "react";

export function Json({ value, max = 420 }: { value: unknown; max?: number }) {
  const [open, setOpen] = useState(false);
  const text = JSON.stringify(value, null, 2) ?? "null";
  const long = text.split("\n").length > 24;
  return (
    <div>
      <pre className="json" style={{ maxHeight: open ? 900 : max }}>
        {text}
      </pre>
      {long && (
        <a className="sm" onClick={() => setOpen(!open)}>
          {open ? "show less" : "show all"}
        </a>
      )}
    </div>
  );
}

export function Copy({ text, label = "copy" }: { text: string; label?: string }) {
  const [done, setDone] = useState(false);
  return (
    <a
      className="sm"
      onClick={() => {
        navigator.clipboard?.writeText(text);
        setDone(true);
        setTimeout(() => setDone(false), 1200);
      }}
    >
      {done ? "copied" : label}
    </a>
  );
}
