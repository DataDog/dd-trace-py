from ddtrace.internal.appsec.prototypes import AppsecSpanProcessorProto
from ddtrace.internal.core.event_hub import EventNoArgs


security_processor: EventNoArgs[AppsecSpanProcessorProto] = EventNoArgs()
