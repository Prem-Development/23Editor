import 'package:editor_23/models/capability_status.dart';
import 'package:editor_23/models/editing_plan.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('Capability states parse from backend labels', () {
    expect(capabilityStateFrom('verified'), CapabilityState.verified);
    expect(
      capabilityStateFrom('implemented_but_untested'),
      CapabilityState.implementedButUntested,
    );
    expect(capabilityStateFrom('planned'), CapabilityState.planned);
    expect(capabilityStateFrom('unavailable'), CapabilityState.unavailable);
    expect(capabilityStateFrom('bogus'), CapabilityState.unavailable);
  });

  test('BackendStatus parses integrations', () {
    final BackendStatus status = BackendStatus.fromJson({
      'apiKeyPresent': true,
      'apiAccess': 'verified',
      'ffmpeg': {'present': true, 'version': 'ffmpeg 7.1'},
      'integrations': [
        {'id': 'spark_plan', 'status': 'verified', 'detail': 'Plans work.'},
        {'id': 'open_weight', 'status': 'unavailable', 'detail': 'No GPU.'},
      ],
    });
    expect(status.apiKeyPresent, isTrue);
    expect(status.ffmpegPresent, isTrue);
    expect(status.integrations, hasLength(2));
    expect(status.integrations[0].title, contains('Spark'));
    expect(status.integrations[0].state, CapabilityState.verified);
    expect(status.integrations[1].state, CapabilityState.unavailable);
  });

  test('EditingPlan describes operations honestly', () {
    final EditingPlan plan = EditingPlan.fromJson(
      {
        'operations': [
          {'op': 'trim', 'start': 5, 'end': 50},
          {'op': 'frame_subject', 'subject': 'dog', 'start': 5, 'end': 20},
        ],
        'notes': 'Cut intro.',
      },
      'muse-spark-1.3',
      ['Gap warning'],
    );
    expect(plan.operations, hasLength(2));
    expect(plan.operations[0].label, 'Trim');
    expect(plan.operations[0].describe(), contains('5.0s'));
    expect(plan.operations[1].label, 'Track subject');
    expect(plan.model, 'muse-spark-1.3');
    expect(plan.warnings, ['Gap warning']);
  });
}
