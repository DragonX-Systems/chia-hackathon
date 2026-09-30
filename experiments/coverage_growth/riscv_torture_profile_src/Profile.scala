package torture

import java.util.Properties

/** Per-invocation settings: reset on every generator command in the sbt JVM. */
object Profile {
  var mulDivOnly = false
  var atomicPercent = 0
  var fencePercent = 0

  def configure(p: Properties): Unit = {
    mulDivOnly = p.getProperty("torture.generator.profile.muldiv_only", "false").toBoolean
    atomicPercent = p.getProperty("torture.generator.profile.atomic_percent", "0").toInt
    fencePercent = p.getProperty("torture.generator.profile.fence_percent", "0").toInt
    require(atomicPercent >= 0 && fencePercent >= 0 && atomicPercent + fencePercent <= 100)
    require(atomicPercent == 0 || p.getProperty("torture.generator.amo", "false").toBoolean)
    require(!mulDivOnly || p.getProperty("torture.generator.mul", "false").toBoolean ||
            p.getProperty("torture.generator.divider", "false").toBoolean)
  }
}
