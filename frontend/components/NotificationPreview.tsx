"use client";
// IA-Notificação (F-031): copy gerada de e-mail p/ o evento do pedido (confirmação/enviado),
// reaproveitando a notificação simulada (F-005). Exibida como uma "notification preview" estilo
// e-mail na confirmação do checkout e no detalhe do pedido. Mostra só o conteúdo gerado.
// Backend resolve a ordem (grounding real) e honra os toggles; offline → fallback gracioso.
// Estilizado pelas paletas.
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

  if (failed) return null; // silencioso: a notificação é um extra (o status já está visível)

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
