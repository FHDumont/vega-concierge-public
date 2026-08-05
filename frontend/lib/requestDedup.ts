/** Dedup in-flight fetches (React Strict Mode remounts the same effect twice in dev). */
const inflight = new Map<string, Promise<unknown>>();

export function dedupedFetch<T>(key: string, fn: () => Promise<T>): Promise<T> {
  const existing = inflight.get(key);
  if (existing) {
    return existing as Promise<T>;
  }
  const promise = fn().finally(() => {
    inflight.delete(key);
  });
  inflight.set(key, promise);
  return promise;
}
