# Cost guard for the whole account. A budget alert does not stop anything; it
# is the early warning that something was left running. Threshold notifications
# go to the owner's email at 50 % and 100 % of the monthly amount.
resource "aws_budgets_budget" "demo" {
  name         = "mcpgw-demo-monthly"
  budget_type  = "COST"
  limit_amount = var.monthly_budget_usd
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  dynamic "notification" {
    for_each = [50, 100]
    content {
      comparison_operator        = "GREATER_THAN"
      threshold                  = notification.value
      threshold_type             = "PERCENTAGE"
      notification_type          = "ACTUAL"
      subscriber_email_addresses = [var.alert_email]
    }
  }
}
