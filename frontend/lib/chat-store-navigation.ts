// Closes the floating panel when the reply asks for login or checkout (F-WORKSHOP-UC-CARDS).
import type { AnswerLayout } from "@/components/AnswerLayout";
import type { ChatResult } from "@/lib/api";

const SIGN_IN_RE = /\b(?:sign[\s-]?in|log[\s-]?in)\b/i;
const CHECKOUT_DIRECT_RE =
  /\b(?:go to|head to|continue to|proceed to|use the|open|complete)\b[\s\S]{0,40}\bcheckout\b/i;
const CHECKOUT_HINT_RE = /\bcheckout\b[\s\S]{0,24}\b(?:page|now|to complete|to finish)\b/i;

function layoutPromptsSignIn(layout: unknown): boolean {
  const facts = (layout as AnswerLayout | undefined)?.facts;
  if (!facts?.length) return false;
  return facts.some((f) => SIGN_IN_RE.test(f.value));
}

/** True when the assistant directs the shopper to sign in or go to checkout. */
export function shouldCloseChatForStoreNavigation(result: ChatResult): boolean {
  const action = result.artifacts?.store_action;
  if (action === "sign_in" || action === "checkout") return true;

  const reply = result.reply || "";
  if (SIGN_IN_RE.test(reply)) return true;
  if (CHECKOUT_DIRECT_RE.test(reply) || CHECKOUT_HINT_RE.test(reply)) return true;
  if (layoutPromptsSignIn(result.artifacts?.layout)) return true;

  return false;
}
