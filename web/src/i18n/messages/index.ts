import { authEn, authMm } from "@/i18n/messages/auth";
import { commonEn, commonMm } from "@/i18n/messages/common";
import { financeEn, financeMm } from "@/i18n/messages/finance";
import { navEn, navMm } from "@/i18n/messages/nav";
import { operationsEn, operationsMm } from "@/i18n/messages/operations";
import { platformEn, platformMm } from "@/i18n/messages/platform";
import { ptEn, ptMm } from "@/i18n/messages/pt";
import { reportsEn, reportsMm } from "@/i18n/messages/reports";

function merge(
  ...groups: ReadonlyArray<Record<string, string>>
): Record<string, string> {
  return Object.assign({}, ...groups);
}

export const messages = {
  en: merge(
    commonEn,
    authEn,
    navEn,
    operationsEn,
    financeEn,
    ptEn,
    reportsEn,
    platformEn,
  ),
  mm: merge(
    commonMm,
    authMm,
    navMm,
    operationsMm,
    financeMm,
    ptMm,
    reportsMm,
    platformMm,
  ),
};
