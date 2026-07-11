"use client";

import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { configApi } from "@/lib/api/config";

export function useAppCapabilities() {
  const [researchTeamEnabled, setResearchTeamEnabled] = useState(false);
  const [loading, setLoading] = useState(true);
  const notifiedRef = useRef(false);

  useEffect(() => {
    let active = true;
    configApi
      .getCapabilities()
      .then((capabilities) => {
        if (active) setResearchTeamEnabled(capabilities.research_team);
      })
      .catch(() => {
        if (active && !notifiedRef.current) {
          notifiedRef.current = true;
          toast.error("无法获取 Agent 模式能力");
        }
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  return { researchTeamEnabled, loading };
}
