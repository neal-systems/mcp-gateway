# Host-level monitoring for one instance: the two EC2 status checks feed one
# alarm each, and both notify the owner by email through one SNS topic. The
# subscription needs a one-time confirmation click in the email.
resource "aws_sns_topic" "alerts" {
  name = "${local.name}-alerts"
}

resource "aws_sns_topic_subscription" "owner_email" {
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

resource "aws_cloudwatch_metric_alarm" "status_check" {
  for_each = {
    instance = "StatusCheckFailed_Instance"
    system   = "StatusCheckFailed_System"
  }

  alarm_name          = "${local.name}-${each.key}-status-check"
  alarm_description   = "EC2 ${each.key} status check failed on the demo host"
  namespace           = "AWS/EC2"
  metric_name         = each.value
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 2
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]

  dimensions = {
    InstanceId = aws_instance.this.id
  }
}
