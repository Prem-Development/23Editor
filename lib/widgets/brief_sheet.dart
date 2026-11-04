import 'package:editor_23/services/backend_client.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

/// Goal options for the in-app editing brief.
enum BriefGoal {
  captions('captions', 'Captions'),
  product('product', 'Product ad'),
  reel('reel', 'Social reel'),
  tutorial('tutorial', 'Tutorial'),
  travel('travel', 'Travel highlights'),
  custom('custom', 'Custom edit'),
  reference('reference', 'Match reference');

  final String value;
  final String label;

  const BriefGoal(this.value, this.label);
}

/// Brief language options. Speech recognition supports Tamil, English,
/// and mixed speech; brief text is offered in the same three only.
enum BriefLanguage {
  en('en', 'English'),
  ta('ta', 'Tamil'),
  mixed('mixed', 'Mixed');

  final String value;
  final String label;

  const BriefLanguage(this.value, this.label);
}

/// Bottom sheet that builds an editing brief inside the app: the user
/// picks a goal, speaks or types naturally, reviews the proposed brief,
/// and feeds it to the validated pipeline with "Use this edit".
class BriefSheet extends StatefulWidget {
  final BackendClient client;
  final Map<String, dynamic>? analysis;
  final Map<String, dynamic>? referenceStyle;
  final BriefGoal initialGoal;
  final String initialDetail;
  final ValueChanged<String> onUse;

  const BriefSheet({
    super.key,
    required this.client,
    required this.onUse,
    this.analysis,
    this.referenceStyle,
    this.initialGoal = BriefGoal.custom,
    this.initialDetail = '',
  });

  @override
  State<BriefSheet> createState() => _BriefSheetState();
}

class _BriefSheetState extends State<BriefSheet> {
  late BriefGoal _goal;
  BriefLanguage _language = BriefLanguage.en;
  late final TextEditingController _detailController;
  late final TextEditingController _briefController;
  bool _busy = false;
  List<String> _explains = const <String>[];
  String? _question;
  String? _error;

  @override
  void initState() {
    super.initState();
    _goal = widget.initialGoal;
    _detailController = TextEditingController(text: widget.initialDetail);
    _briefController = TextEditingController();
  }

  @override
  void dispose() {
    _detailController.dispose();
    _briefController.dispose();
    super.dispose();
  }

  Future<void> _writeBrief() async {
    if (_busy) {
      return;
    }
    setState(() {
      _busy = true;
      _error = null;
      _question = null;
    });
    try {
      final Map<String, dynamic> body = <String, dynamic>{
        'goal': _goal.value,
        'language': _language.value,
        'detail': _detailController.text.trim(),
      };
      if (widget.analysis != null) {
        body['analysis'] = widget.analysis;
      }
      if (widget.referenceStyle != null) {
        body['reference_style'] = widget.referenceStyle;
      }
      final Map<String, dynamic> json = await widget.client.postJson(
        '/api/brief',
        body,
        timeout: const Duration(minutes: 3),
      );
      if (!mounted) {
        return;
      }
      final String? question = json['question'] as String?;
      if (question != null && question.isNotEmpty) {
        setState(() {
          _busy = false;
          _question = question;
        });
        return;
      }
      final List<String> explains = ((json['explains'] as List?) ?? const [])
          .whereType<String>()
          .toList();
      setState(() {
        _busy = false;
        _briefController.text = json['brief'] as String? ?? '';
        _explains = explains;
      });
    } on BackendException catch (error) {
      if (mounted) {
        setState(() {
          _busy = false;
          _error = error.details['question'] as String? ?? error.message;
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final TextTheme text = Theme.of(context).textTheme;
    return SafeArea(
      child: Padding(
        padding: EdgeInsets.only(
          left: 20,
          right: 20,
          top: 16,
          bottom: MediaQuery.of(context).viewInsets.bottom + 20,
        ),
        child: SingleChildScrollView(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: <Widget>[
              Text(
                'Help me describe my edit',
                key: const ValueKey<String>('briefSheetTitle'),
                style: text.titleLarge,
              ),
              const SizedBox(height: 4),
              Text(
                'Pick a goal and describe it naturally. The brief uses '
                'your footage analysis and stays editable.',
                style: text.bodySmall,
              ),
              const SizedBox(height: 12),
              Wrap(
                spacing: 8,
                runSpacing: 8,
                children: <Widget>[
                  for (final BriefGoal goal in BriefGoal.values)
                    ChoiceChip(
                      label: Text(goal.label),
                      selected: _goal == goal,
                      onSelected: (_) => setState(() => _goal = goal),
                    ),
                ],
              ),
              const SizedBox(height: 12),
              SegmentedButton<BriefLanguage>(
                segments: <ButtonSegment<BriefLanguage>>[
                  for (final BriefLanguage language in BriefLanguage.values)
                    ButtonSegment<BriefLanguage>(
                      value: language,
                      label: Text(language.label),
                    ),
                ],
                selected: <BriefLanguage>{_language},
                onSelectionChanged: (Set<BriefLanguage> selected) =>
                    setState(() => _language = selected.first),
              ),
              const SizedBox(height: 4),
              Text(
                'Speech and brief text support Tamil, English, and mixed '
                'speech. Voice instructions set the goal; source-video '
                'captions stay separate.',
                style: text.bodySmall,
              ),
              const SizedBox(height: 12),
              TextField(
                controller: _detailController,
                minLines: 2,
                maxLines: 4,
                decoration: const InputDecoration(
                  labelText: 'What should the edit do?',
                  hintText: 'For example: keep the funny parts, add titles',
                ),
              ),
              const SizedBox(height: 12),
              FilledButton.icon(
                key: const ValueKey<String>('writeBriefButton'),
                onPressed: _busy ? null : _writeBrief,
                icon: const Icon(Icons.edit_note_outlined),
                label: Text(_busy ? 'Writing…' : 'Write my brief'),
              ),
              if (_question != null) ...<Widget>[
                const SizedBox(height: 12),
                Text(
                  _question!,
                  style: text.bodyMedium?.copyWith(fontWeight: FontWeight.w600),
                ),
                const SizedBox(height: 4),
                Text(
                  'Answer above, then tap Write my brief again.',
                  style: text.bodySmall,
                ),
              ],
              if (_error != null) ...<Widget>[
                const SizedBox(height: 12),
                Text(
                  _error!,
                  style: text.bodyMedium?.copyWith(
                    color: Theme.of(context).colorScheme.error,
                  ),
                ),
              ],
              if (_briefController.text.isNotEmpty) ...<Widget>[
                const SizedBox(height: 12),
                Text('Your editing brief', style: text.titleSmall),
                const SizedBox(height: 4),
                TextField(
                  controller: _briefController,
                  minLines: 3,
                  maxLines: 6,
                  decoration: const InputDecoration(
                    labelText: 'Brief (editable)',
                  ),
                ),
                for (final String line in _explains) ...<Widget>[
                  const SizedBox(height: 4),
                  Row(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: <Widget>[
                      const Text('• '),
                      Expanded(child: Text(line, style: text.bodySmall)),
                    ],
                  ),
                ],
                const SizedBox(height: 12),
                Wrap(
                  spacing: 8,
                  runSpacing: 8,
                  children: <Widget>[
                    OutlinedButton.icon(
                      onPressed: () {
                        Clipboard.setData(
                          ClipboardData(text: _briefController.text),
                        );
                        ScaffoldMessenger.of(context).showSnackBar(
                          const SnackBar(content: Text('Brief copied.')),
                        );
                      },
                      icon: const Icon(Icons.copy_outlined),
                      label: const Text('Copy'),
                    ),
                    FilledButton.icon(
                      key: const ValueKey<String>('useBriefButton'),
                      onPressed: () {
                        widget.onUse(_briefController.text);
                        Navigator.of(context).pop();
                      },
                      icon: const Icon(Icons.check_outlined),
                      label: const Text('Use this edit'),
                    ),
                  ],
                ),
              ],
            ],
          ),
        ),
      ),
    );
  }
}
