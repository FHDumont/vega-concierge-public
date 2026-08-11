"use client";
// Notification AI (F-031): email copy generated for the order event (confirmed/shipped),
// reusing the simulated notification (F-005). Shown as an email-style "notification preview"
// on checkout confirmation and on the order detail page. Shows only the generated content.
// Backend resolves the order (real grounding) and honors the toggles; offline → graceful fallback.
// Styled via the palettes.
import { useEffect, useState } from "react";
import { NotificationCopy, orderNotification } from "@/lib/api";
import { dedupedFetch } from "@/lib/requestDedup";

export default function NotificationPreview({ orderId }: { orderId: string }) {
  const [copy, setCopy] = useState<NotificationCopy | null>(null);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setCopy(null);
    setFailed(false);
    dedupedFetch(`order-notification:${orderId}`, () => orderNotification(orderId))
      .then((d) => alive && setCopy(d))
      .catch(() => alive && setFailed(true))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [orderId]);

  if (failed) return null; // silent: the notification is an extra (status is already visible)

  return (
    <div className="ns-notify" aria-label="Order notification preview">
      <div className="ns-notify-head">
        <span className="ns-spark sm" aria-hidden>✦</span>
        <span className="chan">Email preview</span>
      </div>
      {loading ? (
        <>
          <span className="ns-skel ns-pulse bar" style={{ height: 14, width: "55%" }} />
          <span className="ns-skel ns-pulse bar" style={{ height: 12, width: "85%", marginTop: 8 }} />
        </>
      ) : copy ? (
        <>
          <p className="subj">{copy.subject}</p>
          <p className="body">{copy.body}</p>
        </>
      ) : null}
    </div>
  );
}
