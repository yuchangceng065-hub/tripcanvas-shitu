export interface Mention {
  text: string;
  role: "visit" | "destination" | "origin";
}
export const mentionKey = (mention: Mention) =>
  `${mention.role}:${mention.text}`;
// Preserve independent manual and mention references when changing a resolution.
export function candidateSelection(
  manual: string[],
  mentions: Mention[],
  chosen: Record<string, string>,
  endpoints: (string | null | undefined)[],
) {
  return [
    ...new Set([
      ...manual,
      ...mentions
        .filter((m) => m.role === "visit")
        .map((m) => chosen[mentionKey(m)])
        .filter((id): id is string => !!id),
    ]),
  ].filter((id) => !endpoints.includes(id));
}
// Import suggestions may add preferences, but never downgrade an existing must.
export function mergeImportedOptional(
  existing: string[],
  optional: string[],
  incoming: string[],
  incomingOptional: string[],
) {
  const must = new Set([
    ...existing.filter((id) => !optional.includes(id)),
    ...incoming.filter((id) => !incomingOptional.includes(id)),
  ]);
  return [...new Set([...optional, ...incomingOptional])].filter(
    (id) => !must.has(id),
  );
}
