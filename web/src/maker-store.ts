import { mergeSeals } from "./maker-cache";
import type { SealDef } from "./seal-geo";

export type Pending = { key: string; id?: string; label: string; why: string; real: string;
  prompt?: string; photo?: Blob; started: number };
const COLLECTION = "wanwuyin.collection.v1";

function database(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open("wanwuyin-maker", 1);
    request.onupgradeneeded = () => request.result.createObjectStore("state");
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

async function pendingStore<T>(mode: IDBTransactionMode, action: (store: IDBObjectStore) => IDBRequest<T>): Promise<T> {
  const db = await database();
  return new Promise((resolve, reject) => {
    const transaction = db.transaction("state", mode), request = action(transaction.objectStore("state"));
    transaction.oncomplete = () => { db.close(); resolve(request.result); };
    transaction.onerror = () => { db.close(); reject(transaction.error); };
    transaction.onabort = () => { db.close(); reject(transaction.error); };
  });
}

export const savePending = (value: Pending) => pendingStore("readwrite", (store) => store.put(value, "pending"));
export const loadPending = () => pendingStore<Pending | undefined>("readonly", (store) => store.get("pending"));
export const clearPending = () => pendingStore("readwrite", (store) => store.delete("pending"));

export function collection(): SealDef[] {
  try { return mergeSeals(JSON.parse(localStorage.getItem(COLLECTION) ?? "[]")); } catch { return []; }
}

export function collect(seal: SealDef): SealDef[] {
  const seals = mergeSeals(collection(), [seal]);
  localStorage.setItem(COLLECTION, JSON.stringify(seals));
  return seals;
}
