import { useState, useEffect, useCallback } from "react";
import { Modal, Radio, Alert } from "@agentscope-ai/design";
import { useTranslation } from "react-i18next";
import { TenantTargetPicker } from "../../../../components/TenantTargetPicker";
import api from "../../../../api";
import type { AgentConfigDistributionTenantResult } from "../../../../api/types/agent";
import styles from "./DistributeModal.module.less";

interface DistributeModalProps {
  open: boolean;
  configGroup: string;
  configGroupLabel: string;
  onClose: () => void;
  onSuccess?: () => void;
}

export function DistributeModal({
  open,
  configGroup,
  configGroupLabel,
  onClose,
  onSuccess,
}: DistributeModalProps) {
  const { t } = useTranslation();
  const [tenantIds, setTenantIds] = useState<string[]>([]);
  const [selectedTenantIds, setSelectedTenantIds] = useState<string[]>([]);
  const [overwrite, setOverwrite] = useState(true);
  const [loading, setLoading] = useState(false);
  const [distributing, setDistributing] = useState(false);

  // 打开时加载租户列表
  useEffect(() => {
    if (!open) return;
    setLoading(true);
    setSelectedTenantIds([]);
    api
      .listAgentConfigDistributionTenants()
      .then((result) => {
        setTenantIds(result.tenant_ids || []);
      })
      .catch((err) => {
        const errMsg = err instanceof Error ? err.message : String(err);
        Modal.error({ title: t("common.error"), content: errMsg });
      })
      .finally(() => {
        setLoading(false);
      });
  }, [open, t]);

  const handleDistribute = useCallback(async () => {
    if (!selectedTenantIds.length) return;
    setDistributing(true);
    try {
      const result = await api.distributeAgentConfig({
        config_groups: [configGroup],
        target_tenant_ids: selectedTenantIds,
        overwrite,
      });
      const items: AgentConfigDistributionTenantResult[] =
        Array.isArray(result.results) ? result.results : [];
      const succeeded = items.filter((item) => item.success);
      const failed = items.filter((item) => !item.success);

      if (succeeded.length > 0) {
        const lines = succeeded.map((item) => {
          const suffix = item.bootstrapped
            ? ` (${t("mcp.distributeBootstrapped")})`
            : "";
          return `• ${item.tenant_id}${suffix}`;
        });
        Modal.confirm({
          title: t("mcp.distributeResultTitle"),
          content: (
            <div style={{ display: "grid", gap: 8 }}>
              <div>{t("mcp.distributeSuccessList")}</div>
              <pre style={{ margin: 0, whiteSpace: "pre-wrap" }}>
                {lines.join("\n")}
              </pre>
              {failed.length > 0 ? (
                <div>{t("mcp.distributeFailureInlineHint")}</div>
              ) : null}
            </div>
          ),
          okText: t("common.close"),
          cancelButtonProps: { style: { display: "none" } },
        });
      }

      if (failed.length > 0) {
        const failureLines = failed.map(
          (item) =>
            `• ${item.tenant_id}: ${item.error || t("mcp.distributeFailed")}`,
        );
        if (succeeded.length === 0) {
          Modal.error({
            title: t("mcp.distributeFailed"),
            content: (
              <pre style={{ margin: 0, whiteSpace: "pre-wrap" }}>
                {failureLines.join("\n")}
              </pre>
            ),
          });
        } else {
          Modal.confirm({
            title: t("mcp.distributePartialFailureTitle"),
            content: (
              <pre style={{ margin: 0, whiteSpace: "pre-wrap" }}>
                {failureLines.join("\n")}
              </pre>
            ),
            okText: t("common.close"),
            cancelButtonProps: { style: { display: "none" } },
          });
        }
      }

      onClose();
      onSuccess?.();
    } catch (err) {
      const errMsg = err instanceof Error ? err.message : String(err);
      Modal.error({ title: t("mcp.distributeFailed"), content: errMsg });
    } finally {
      setDistributing(false);
    }
  }, [configGroup, selectedTenantIds, overwrite, onClose, onSuccess, t]);

  return (
    <Modal
      open={open}
      title={t("agentConfig.distributeTitle", { label: configGroupLabel })}
      onCancel={onClose}
      onOk={handleDistribute}
      okText={t("agentConfig.distributeConfirm")}
      cancelText={t("common.cancel")}
      confirmLoading={distributing}
      okButtonProps={{ disabled: !selectedTenantIds.length || loading }}
      width={640}
    >
      <div className={styles.modalContent}>
        <Alert
          type="warning"
          message={t("agentConfig.distributeWarning")}
          showIcon
          style={{ marginBottom: 16 }}
        />

        <div className={styles.strategySection}>
          <div className={styles.strategyLabel}>
            {t("agentConfig.distributeStrategy")}
          </div>
          <Radio.Group
            value={overwrite}
            onChange={(e) => setOverwrite(e.target.value)}
          >
            <Radio value={true}>
              {t("agentConfig.distributeOverwrite")}
            </Radio>
            <Radio value={false}>
              {t("agentConfig.distributeFillEmpty")}
            </Radio>
          </Radio.Group>
        </div>

        <TenantTargetPicker
          tenantIds={tenantIds}
          selectedTenantIds={selectedTenantIds}
          onChange={setSelectedTenantIds}
        />
      </div>
    </Modal>
  );
}