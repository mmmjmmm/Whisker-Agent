import { get, post } from "./fetch";
import type {
  SkillDetail,
  SkillListItem,
  SkillsData,
} from "./types";

export const skillApi = {
  list: (): Promise<SkillsData> => get<SkillsData>("/app-config/skills"),

  detail: (id: string): Promise<SkillDetail> =>
    get<SkillDetail>("/app-config/skills/" + id),

  upload: (file: File): Promise<SkillListItem> => {
    const form = new FormData();
    form.append("file", file);
    return post<SkillListItem>("/app-config/skills", form);
  },

  setEnabled: (id: string, enabled: boolean): Promise<SkillListItem> =>
    post<SkillListItem>("/app-config/skills/" + id + "/enabled", {
      enabled,
    }),

  delete: (id: string): Promise<void> =>
    post<void>("/app-config/skills/" + id + "/delete", {}),
};
