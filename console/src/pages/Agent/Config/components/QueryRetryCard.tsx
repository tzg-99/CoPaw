import { Card, Form, InputNumber, Switch } from "@agentscope-ai/design";
import { useTranslation } from "react-i18next";
import styles from "../index.module.less";

interface QueryRetryCardProps {
  queryRetryEnabled?: boolean;
}

export function QueryRetryCard({
  queryRetryEnabled = false,
}: QueryRetryCardProps) {
  const { t } = useTranslation();
  const form = Form.useFormInstance();

  return (
    <Card
      className={styles.formCard}
      title={t("agentConfig.queryRetryTitle")}
      style={{ marginTop: 16 }}
    >
      <Form.Item
        name={["query_retry", "enabled"]}
        label={t("agentConfig.queryRetryEnabled")}
        valuePropName="checked"
        tooltip={t("agentConfig.queryRetryEnabledTooltip")}
      >
        <Switch />
      </Form.Item>

      <div className={styles.llmRetryRow}>
        <Form.Item
          label={t("agentConfig.queryRetryMaxRetries")}
          name={["query_retry", "max_retries"]}
          rules={[
            {
              required: true,
              message: t("agentConfig.queryRetryMaxRetriesRequired"),
            },
            {
              type: "number",
              min: 1,
              message: t("agentConfig.queryRetryMaxRetriesMin"),
            },
          ]}
          tooltip={t("agentConfig.queryRetryMaxRetriesTooltip")}
          className={styles.llmRetryField}
        >
          <InputNumber
            style={{ width: "100%" }}
            min={1}
            step={1}
            disabled={!queryRetryEnabled}
            placeholder={t("agentConfig.queryRetryMaxRetriesPlaceholder")}
          />
        </Form.Item>

        <Form.Item
          label={t("agentConfig.queryRetryBackoffBase")}
          name={["query_retry", "backoff_base"]}
          rules={[
            {
              required: true,
              message: t("agentConfig.queryRetryBackoffBaseRequired"),
            },
            {
              type: "number",
              min: 0.5,
              message: t("agentConfig.queryRetryBackoffBaseMin"),
            },
          ]}
          tooltip={t("agentConfig.queryRetryBackoffBaseTooltip")}
          className={styles.llmRetryField}
        >
          <InputNumber
            style={{ width: "100%" }}
            step={0.1}
            min={0.5}
            disabled={!queryRetryEnabled}
            placeholder={t("agentConfig.queryRetryBackoffBasePlaceholder")}
          />
        </Form.Item>

        <Form.Item
          label={t("agentConfig.queryRetryBackoffCap")}
          name={["query_retry", "backoff_cap"]}
          dependencies={[["query_retry", "backoff_base"]]}
          rules={[
            {
              required: true,
              message: t("agentConfig.queryRetryBackoffCapRequired"),
            },
            {
              type: "number",
              min: 1.0,
              message: t("agentConfig.queryRetryBackoffCapMin"),
            },
            {
              validator: async (_, value) => {
                const backoffBase = form.getFieldValue([
                  "query_retry",
                  "backoff_base",
                ]);
                if (
                  typeof value !== "number" ||
                  typeof backoffBase !== "number" ||
                  value >= backoffBase
                ) {
                  return;
                }
                throw new Error(t("agentConfig.queryRetryBackoffCapGteBase"));
              },
            },
          ]}
          tooltip={t("agentConfig.queryRetryBackoffCapTooltip")}
          className={styles.llmRetryField}
        >
          <InputNumber
            style={{ width: "100%" }}
            step={0.5}
            min={1.0}
            disabled={!queryRetryEnabled}
            placeholder={t("agentConfig.queryRetryBackoffCapPlaceholder")}
          />
        </Form.Item>
      </div>
    </Card>
  );
}
