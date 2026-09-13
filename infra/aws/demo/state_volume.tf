# OAuth client registrations and the generated JWT signing key live here rather
# than on the root volume, so replacing the instance does not sign every issued
# token out of existence.
#
# There is deliberately no prevent_destroy and no retain flag: a demo has to be
# destroyable with one `terraform destroy`, and a lifecycle block that survives
# teardown would leave an orphaned volume billing quietly. The cost of that
# choice is that destroy really does discard the signing key and every client
# registration, which is the intended behaviour for a demo.
resource "aws_ebs_volume" "state" {
  availability_zone = local.az
  size              = var.state_volume_gb
  type              = "gp3"
  encrypted         = true # default aws/ebs key

  tags = {
    Name = "${local.name}-state"
    Role = "state"
  }
}

resource "aws_volume_attachment" "state" {
  device_name = "/dev/xvdf"
  volume_id   = aws_ebs_volume.state.id
  instance_id = aws_instance.this.id

  # Detaching a mounted filesystem from a running instance corrupts it. Let AWS
  # stop the instance first; a demo can afford the downtime.
  stop_instance_before_detaching = true
}
