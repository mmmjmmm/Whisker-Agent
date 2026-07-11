import { ExternalLink, FileSearch } from "lucide-react";

import type { ResearchSource } from "@/lib/api/types";
import { cn } from "@/lib/utils";

type ResearchSourceListProps = {
  sources: ResearchSource[];
  className?: string;
};

function safeHttpUrl(source: ResearchSource): string | null {
  for (const candidate of [source.canonical_url, source.original_url]) {
    try {
      const url = new URL(candidate);
      if (url.protocol === "https:" || url.protocol === "http:") {
        return url.toString();
      }
    } catch {
      // Invalid source URLs remain visible as text but are not clickable.
    }
  }
  return null;
}

function supportedClaimCount(source: ResearchSource): number | null {
  const count = source.metadata.supported_claim_count;
  return typeof count === "number" && Number.isFinite(count) ? count : null;
}

function publishedDate(value: string | null): string | null {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(date);
}

export function ResearchSourceList({
  sources,
  className,
}: ResearchSourceListProps) {
  if (sources.length === 0) {
    return (
      <div
        className={cn(
          "flex items-center justify-center gap-2 py-5 text-sm text-gray-500",
          className,
        )}
      >
        <FileSearch className="size-4" aria-hidden="true" />
        <span>尚未收集来源</span>
      </div>
    );
  }

  return (
    <ul className={cn("min-w-0 divide-y divide-gray-100", className)}>
      {sources.map((source) => {
        const href = safeHttpUrl(source);
        const claimCount = supportedClaimCount(source);
        const date = publishedDate(source.published_at);

        return (
          <li key={source.id} className="min-w-0 py-2.5">
            <div className="flex min-w-0 items-start justify-between gap-3">
              <div className="min-w-0 flex-1">
                {href ? (
                  <a
                    href={href}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex max-w-full items-start gap-1 text-sm font-medium text-gray-900 hover:text-blue-700 hover:underline"
                  >
                    <span className="min-w-0 break-words">{source.title}</span>
                    <ExternalLink
                      className="mt-0.5 size-3.5 shrink-0"
                      aria-hidden="true"
                    />
                  </a>
                ) : (
                  <p className="break-words text-sm font-medium text-gray-900">
                    {source.title}
                  </p>
                )}
                <div className="mt-1 flex flex-wrap gap-x-2 gap-y-1 text-xs text-gray-500">
                  <span>{source.domain}</span>
                  <span>{source.source_class}</span>
                  {date && <span>{date}</span>}
                  {claimCount !== null && (
                    <span>支持 {claimCount} 条结论</span>
                  )}
                </div>
              </div>
            </div>
          </li>
        );
      })}
    </ul>
  );
}
