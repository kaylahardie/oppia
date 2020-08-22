# Copyright 2018 The Oppia Authors. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS-IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Funtions to create, accept, reject, update and perform other operations on
suggestions.
"""

from __future__ import absolute_import  # pylint: disable=import-only-modules
from __future__ import unicode_literals  # pylint: disable=import-only-modules

import functools

from core.domain import email_manager
from core.domain import exp_fetchers
from core.domain import feedback_services
from core.domain import fs_services
from core.domain import html_validation_service
from core.domain import image_validation_services
from core.domain import suggestion_registry
from core.domain import user_domain
from core.domain import user_services
from core.platform import models
import feconf
import utils

(feedback_models, suggestion_models, user_models) = (
    models.Registry.import_models(
        [models.NAMES.feedback, models.NAMES.suggestion, models.NAMES.user]))

DEFAULT_SUGGESTION_THREAD_SUBJECT = 'Suggestion from a user'
DEFAULT_SUGGESTION_THREAD_INITIAL_MESSAGE = ''


def create_suggestion(
        suggestion_type, target_type, target_id, target_version_at_submission,
        author_id, change, description):
    """Creates a new SuggestionModel and the corresponding FeedbackThread.

    Args:
        suggestion_type: str. The type of the suggestion. This parameter should
            be one of the constants defined in storage/suggestion/gae_models.py.
        target_type: str. The target entity being edited. This parameter should
            be one of the constants defined in storage/suggestion/gae_models.py.

        target_id: str. The ID of the target entity being suggested to.
        target_version_at_submission: int. The version number of the target
            entity at the time of creation of the suggestion.
        author_id: str. The ID of the user who submitted the suggestion.
        change: dict. The details of the suggestion.
        description: str. The description of the changes provided by the author.

    Returns:
        Suggestion. The newly created suggestion domain object.
    """
    if description is None:
        description = DEFAULT_SUGGESTION_THREAD_SUBJECT
    thread_id = feedback_services.create_thread(
        target_type, target_id, author_id, description,
        DEFAULT_SUGGESTION_THREAD_INITIAL_MESSAGE, has_suggestion=True)

    status = suggestion_models.STATUS_IN_REVIEW

    if target_type == suggestion_models.TARGET_TYPE_EXPLORATION:
        exploration = exp_fetchers.get_exploration_by_id(target_id)
    if suggestion_type == suggestion_models.SUGGESTION_TYPE_EDIT_STATE_CONTENT:
        score_category = (
            suggestion_models.SCORE_TYPE_CONTENT +
            suggestion_models.SCORE_CATEGORY_DELIMITER + exploration.category)
    elif suggestion_type == suggestion_models.SUGGESTION_TYPE_TRANSLATE_CONTENT:
        score_category = (
            suggestion_models.SCORE_TYPE_TRANSLATION +
            suggestion_models.SCORE_CATEGORY_DELIMITER + exploration.category)
        content_html = exploration.get_content_html(
            change['state_name'], change['content_id'])
        if content_html != change['content_html']:
            raise Exception(
                'The given content_html does not match the content of the '
                'exploration.')
    elif suggestion_type == suggestion_models.SUGGESTION_TYPE_ADD_QUESTION:
        score_category = (
            suggestion_models.SCORE_TYPE_QUESTION +
            suggestion_models.SCORE_CATEGORY_DELIMITER + target_id)
    else:
        raise Exception('Invalid suggestion type %s' % suggestion_type)

    suggestion_domain_class = (
        suggestion_registry.SUGGESTION_TYPES_TO_DOMAIN_CLASSES[
            suggestion_type])
    suggestion = suggestion_domain_class(
        thread_id, target_id, target_version_at_submission, status, author_id,
        None, change, score_category)
    suggestion.validate()

    suggestion_models.GeneralSuggestionModel.create(
        suggestion_type, target_type, target_id,
        target_version_at_submission, status, author_id,
        None, change, score_category, thread_id)
    return get_suggestion_by_id(thread_id)


def get_suggestion_from_model(suggestion_model):
    """Converts the given SuggestionModel to a Suggestion domain object

    Args:
        suggestion_model: SuggestionModel. SuggestionModel object to be
            converted to Suggestion domain object.

    Returns:
        Suggestion. The corresponding Suggestion domain object.
    """
    suggestion_domain_class = (
        suggestion_registry.SUGGESTION_TYPES_TO_DOMAIN_CLASSES[
            suggestion_model.suggestion_type])
    return suggestion_domain_class(
        suggestion_model.id, suggestion_model.target_id,
        suggestion_model.target_version_at_submission,
        suggestion_model.status, suggestion_model.author_id,
        suggestion_model.final_reviewer_id, suggestion_model.change_cmd,
        suggestion_model.score_category, suggestion_model.last_updated)


def get_suggestion_by_id(suggestion_id):
    """Finds a suggestion by the suggestion ID.

    Args:
        suggestion_id: str. The ID of the suggestion.

    Returns:
        Suggestion|None. The corresponding suggestion, or None if no suggestion
        is found.
    """
    model = suggestion_models.GeneralSuggestionModel.get_by_id(suggestion_id)

    return get_suggestion_from_model(model) if model else None


def get_suggestions_by_ids(suggestion_ids):
    """Finds suggestions using the given suggestion IDs.

    Args:
        suggestion_ids: list(str). The IDs of the suggestions.

    Returns:
        list(Suggestion|None). A list of the corresponding suggestions. The
        list will contain None elements if no suggestion is found with the
        corresponding suggestion id.
    """
    general_suggestion_models = (
        suggestion_models.GeneralSuggestionModel.get_multi(suggestion_ids)
    )

    return [
        get_suggestion_from_model(suggestion_model) if suggestion_model
        else None for suggestion_model in general_suggestion_models
    ]


def query_suggestions(query_fields_and_values):
    """Queries for suggestions.

    Args:
        query_fields_and_values: list(tuple(str, str)). A list of queries. The
            first element in each tuple is the field to be queried, and the
            second element is its value.

    Returns:
        list(Suggestion). A list of suggestions that match the given query
        values, up to a maximum of feconf.DEFAULT_QUERY_LIMIT suggestions.
    """
    return [
        get_suggestion_from_model(s) for s in
        suggestion_models.GeneralSuggestionModel.query_suggestions(
            query_fields_and_values)
    ]


def get_translation_suggestion_ids_with_exp_ids(exp_ids):
    """Gets the ids of the translation suggestions corresponding to
    explorations with the given exploration ids.

    Args:
        exp_ids: list(str). List of exploration ids to query for.

    Returns:
        list(str). A list of the ids of translation suggestions that
        correspond to the given exploration ids. Note: it is not
        guaranteed that the suggestion ids returned are ordered by the
        exploration ids in exp_ids.
    """
    if len(exp_ids) == 0:
        return []

    return (
        suggestion_models.GeneralSuggestionModel
        .get_translation_suggestion_ids_with_exp_ids(exp_ids)
    )


def get_all_stale_suggestion_ids():
    """Gets a list of the suggestion ids corresponding to suggestions that have
    not had any activity on them for THRESHOLD_TIME_BEFORE_ACCEPT time.

    Returns:
        list(str). A list of suggestion ids that correspond to stale
        suggestions.
    """

    return (
        suggestion_models.GeneralSuggestionModel.get_all_stale_suggestion_ids()
    )


def _update_suggestion(suggestion):
    """Updates the given suggestion.

    Args:
        suggestion: Suggestion. The suggestion to be updated.
    """

    _update_suggestions([suggestion])


def _update_suggestions(suggestions, update_last_updated_time=True):
    """Updates the given suggestions.

    Args:
        suggestions: list(Suggestion). The suggestions to be updated.
        update_last_updated_time: bool. Whether to update the last_updated
            field of the suggestions.
    """
    suggestion_ids = []

    for suggestion in suggestions:
        suggestion.validate()
        suggestion_ids.append(suggestion.suggestion_id)

    suggestion_models_to_update = (
        suggestion_models.GeneralSuggestionModel.get_multi(suggestion_ids)
    )

    for index, suggestion_model in enumerate(suggestion_models_to_update):
        suggestion = suggestions[index]
        suggestion_model.status = suggestion.status
        suggestion_model.final_reviewer_id = suggestion.final_reviewer_id
        suggestion_model.change_cmd = suggestion.change.to_dict()
        suggestion_model.score_category = suggestion.score_category

    suggestion_models.GeneralSuggestionModel.put_multi(
        suggestion_models_to_update,
        update_last_updated_time=update_last_updated_time)


def get_commit_message_for_suggestion(author_username, commit_message):
    """Returns a modified commit message for an accepted suggestion.

    Args:
        author_username: str. Username of the suggestion author.
        commit_message: str. The original commit message submitted by the
            suggestion author.

    Returns:
        str. The modified commit message to be used in the exploration commit
        logs.
    """
    return '%s %s: %s' % (
        feconf.COMMIT_MESSAGE_ACCEPTED_SUGGESTION_PREFIX,
        author_username, commit_message)


def accept_suggestion(
        suggestion_id, reviewer_id, commit_message, review_message):
    """Accepts the suggestion with the given suggestion_id after validating it.

    Args:
        suggestion_id: str. The id of the suggestion to be accepted.
        reviewer_id: str. The ID of the reviewer accepting the suggestion.
        commit_message: str. The commit message.
        review_message: str. The message provided by the reviewer while
            accepting the suggestion.

    Raises:
        Exception. The suggestion is already handled.
        Exception. The suggestion is not valid.
        Exception. The commit message is empty.
    """
    if not commit_message or not commit_message.strip():
        raise Exception('Commit message cannot be empty.')

    suggestion = get_suggestion_by_id(suggestion_id)

    if suggestion is None:
        raise Exception(
            'You cannot accept the suggestion with id %s because it does '
            'not exist.' % (suggestion_id)
        )
    if suggestion.is_handled:
        raise Exception(
            'The suggestion with id %s has already been accepted/'
            'rejected.' % (suggestion_id)
        )
    suggestion.pre_accept_validate()
    html_string = ''.join(suggestion.get_all_html_content_strings())
    error_list = (
        html_validation_service.
        validate_math_tags_in_html_with_attribute_math_content(
            html_string))
    if len(error_list) > 0:
        raise Exception(
            'Invalid math tags found in the suggestion with id %s.' % (
                suggestion.suggestion_id)
        )

    suggestion.set_suggestion_status_to_accepted()
    suggestion.set_final_reviewer_id(reviewer_id)

    author_name = user_services.get_username(suggestion.author_id)
    commit_message = get_commit_message_for_suggestion(
        author_name, commit_message)
    suggestion.accept(commit_message)

    _update_suggestion(suggestion)

    feedback_services.create_message(
        suggestion_id, reviewer_id, feedback_models.STATUS_CHOICES_FIXED,
        None, review_message)

    # When recording of scores is enabled, the author of the suggestion gets an
    # increase in their score for the suggestion category.
    if feconf.ENABLE_RECORDING_OF_SCORES:
        user_id = suggestion.author_id
        score_category = suggestion.score_category

        # Get user scoring domain object.
        user_scoring = get_user_scoring(user_id, score_category)

        # Increment the score of the author due to their suggestion being
        # accepted.
        user_scoring.increment_score(
            suggestion_models.INCREMENT_SCORE_OF_AUTHOR_BY
        )

        # Emails are sent to onboard new reviewers. These new reviewers are
        # created when the score of the user passes the minimum score required
        # to review.
        if feconf.SEND_SUGGESTION_REVIEW_RELATED_EMAILS:
            if user_scoring.can_user_review_category() and (
                    not user_scoring.onboard_reviewer_email_sent):
                email_manager.send_mail_to_onboard_new_reviewers(
                    user_id, score_category
                )
                user_scoring.mark_onboarding_email_as_sent()

        # Need to update the corresponding user scoring model after we updated
        # the domain object.
        _update_user_scoring(user_scoring)


def reject_suggestion(suggestion_id, reviewer_id, review_message):
    """Rejects the suggestion with the given suggestion_id.

    Args:
        suggestion_id: str. The id of the suggestion to be rejected.
        reviewer_id: str. The ID of the reviewer rejecting the suggestion.
        review_message: str. The message provided by the reviewer while
            rejecting the suggestion.

    Raises:
        Exception. The suggestion is already handled.
    """

    reject_suggestions([suggestion_id], reviewer_id, review_message)


def reject_suggestions(suggestion_ids, reviewer_id, review_message):
    """Rejects the suggestions with the given suggestion_ids.

    Args:
        suggestion_ids: list(str). The ids of the suggestions to be rejected.
        reviewer_id: str. The ID of the reviewer rejecting the suggestions.
        review_message: str. The message provided by the reviewer while
            rejecting the suggestions.

    Raises:
        Exception. One or more of the suggestions has already been handled.
    """
    suggestions = get_suggestions_by_ids(suggestion_ids)

    for index, suggestion in enumerate(suggestions):
        if suggestion is None:
            raise Exception(
                'You cannot reject the suggestion with id %s because it does '
                'not exist.' % (suggestion_ids[index])
            )
        if suggestion.is_handled:
            raise Exception(
                'The suggestion with id %s has already been accepted/'
                'rejected.' % (suggestion.suggestion_id)
            )
    if not review_message:
        raise Exception('Review message cannot be empty.')

    for suggestion in suggestions:
        suggestion.set_suggestion_status_to_rejected()
        suggestion.set_final_reviewer_id(reviewer_id)

    _update_suggestions(suggestions)

    feedback_services.create_messages(
        suggestion_ids, reviewer_id, feedback_models.STATUS_CHOICES_IGNORED,
        None, review_message
    )


def auto_reject_question_suggestions_for_skill_id(skill_id):
    """Rejects all SuggestionAddQuestions with target ID matching the supplied
    skill ID. Reviewer ID is set to SUGGESTION_BOT_USER_ID.

    Args:
        skill_id: str. The skill ID corresponding to the target ID of the
            SuggestionAddQuestion.
    """
    suggestions = query_suggestions(
        [
            (
                'suggestion_type',
                suggestion_models.SUGGESTION_TYPE_ADD_QUESTION),
            ('target_id', skill_id)
        ]
    )

    suggestion_ids = [suggestion.suggestion_id for suggestion in suggestions]
    reject_suggestions(
        suggestion_ids, feconf.SUGGESTION_BOT_USER_ID,
        suggestion_models.DELETED_SKILL_REJECT_MESSAGE)


def auto_reject_translation_suggestions_for_exp_ids(exp_ids):
    """Rejects all translation suggestions with target IDs matching the
    supplied exploration IDs. These suggestions are being rejected because
    their corresponding exploration was removed from a story or the story was
    deleted. Reviewer ID is set to SUGGESTION_BOT_USER_ID.

    Args:
        exp_ids: list(str). The exploration IDs corresponding to the target IDs
            of the translation suggestions.
    """
    suggestion_ids = get_translation_suggestion_ids_with_exp_ids(exp_ids)

    reject_suggestions(
        suggestion_ids, feconf.SUGGESTION_BOT_USER_ID,
        suggestion_models.INVALID_STORY_REJECT_TRANSLATION_SUGGESTIONS_MSG)


def resubmit_rejected_suggestion(
        suggestion_id, summary_message, author_id, change):
    """Resubmit a rejected suggestion with the given suggestion_id.

    Args:
        suggestion_id: str. The id of the rejected suggestion.
        summary_message: str. The message provided by the author to
            summarize new suggestion.
        author_id: str. The ID of the author creating the suggestion.
        change: ExplorationChange. The new change to apply to the suggestion.

    Raises:
        Exception. The summary message is empty.
        Exception. The suggestion has not been handled yet.
        Exception. The suggestion has already been accepted.
    """
    suggestion = get_suggestion_by_id(suggestion_id)
    if not summary_message:
        raise Exception('Summary message cannot be empty.')
    if not suggestion.is_handled:
        raise Exception(
            'The suggestion with id %s is not yet handled.' % (suggestion_id)
        )
    if suggestion.status == suggestion_models.STATUS_ACCEPTED:
        raise Exception(
            'The suggestion with id %s was accepted. '
            'Only rejected suggestions can be resubmitted.' % (suggestion_id)
        )

    suggestion.pre_update_validate(change)
    suggestion.change = change
    suggestion.set_suggestion_status_to_in_review()
    _update_suggestion(suggestion)

    feedback_services.create_message(
        suggestion_id, author_id, feedback_models.STATUS_CHOICES_OPEN,
        None, summary_message)


def get_all_suggestions_that_can_be_reviewed_by_user(user_id):
    """Returns a list of suggestions which need to be reviewed, in categories
    where the user has crossed the minimum score to review.

    Args:
        user_id: str. The ID of the user.

    Returns:
        list(Suggestion). A list of suggestions which the given user is allowed
        to review.
    """
    score_categories = (
        user_models.UserContributionScoringModel
        .get_all_categories_where_user_can_review(user_id))

    if len(score_categories) == 0:
        return []

    return ([
        get_suggestion_from_model(s)
        for s in suggestion_models.GeneralSuggestionModel
        .get_in_review_suggestions_in_score_categories(
            score_categories, user_id)
    ])


def get_reviewable_suggestions(user_id, suggestion_type):
    """Returns a list of suggestions of given suggestion_type which the user
    can review.

    Args:
        user_id: str. The ID of the user.
        suggestion_type: str. The type of the suggestion.

    Returns:
        list(Suggestion). A list of suggestions which the given user is allowed
        to review.
    """
    all_suggestions = ([
        get_suggestion_from_model(s) for s in (
            suggestion_models.GeneralSuggestionModel
            .get_in_review_suggestions_of_suggestion_type(
                suggestion_type, user_id))
    ])
    user_review_rights = user_services.get_user_contribution_rights(user_id)
    if suggestion_type == suggestion_models.SUGGESTION_TYPE_TRANSLATE_CONTENT:
        language_codes = (
            user_review_rights.can_review_translation_for_language_codes)
        return [
            suggestion for suggestion in all_suggestions
            if suggestion.change.language_code in language_codes]

    return all_suggestions


def get_submitted_suggestions(user_id, suggestion_type):
    """Returns a list of suggestions of given suggestion_type which the user
    has submitted.

    Args:
        user_id: str. The ID of the user.
        suggestion_type: str. The type of the suggestion.

    Returns:
        list(Suggestion). A list of suggestions which the given user has
        submitted.
    """
    return ([
        get_suggestion_from_model(s) for s in (
            suggestion_models.GeneralSuggestionModel
            .get_user_created_suggestions_of_suggestion_type(
                suggestion_type, user_id))
    ])


def get_user_scoring_from_model(user_scoring_model):
    """Converts the given UserContributionScoringModel to a
    UserContributionScoring domain object.

    Args:
        user_scoring_model: UserContributionScoringModel.
            UserContributionScoringModel to be
            converted to UserContributionScoring domain object.

    Returns:
        UserContributionScoring. The corresponding UserContributionScoring
        domain object.
    """
    return user_domain.UserContributionScoring(
        user_scoring_model.user_id, user_scoring_model.score_category,
        user_scoring_model.score,
        user_scoring_model.onboard_reviewer_email_sent
    )


def _update_user_scoring(user_scoring):
    """Updates the user_scoring.

    Args:
        user_scoring: UserContributionScoring. The user scoring to be
            updated.
    """
    user_scoring_model = user_models.UserContributionScoringModel.get(
        user_scoring.user_id, user_scoring.score_category
    )

    if user_scoring_model:
        user_scoring_model.user_id = user_scoring.user_id
        user_scoring_model.score_category = user_scoring.score_category
        user_scoring_model.score = user_scoring.score
        user_scoring_model.onboard_reviewer_email_sent = (
            user_scoring.onboard_reviewer_email_sent
        )

        user_scoring_model.put()

    else:
        user_models.UserContributionScoringModel.create(
            user_scoring.user_id, user_scoring.score_category,
            user_scoring.score, user_scoring.onboard_reviewer_email_sent)


def get_all_scores_of_user(user_id):
    """Gets all scores for a given user.

    Args:
        user_id: str. The id of the user.

    Returns:
        dict. A dict containing all the scores of the user. The keys of the dict
        are the score categories and the values are the scores.
    """
    scores = {}
    for model in (
            user_models.UserContributionScoringModel.get_all_scores_of_user(
                user_id)):
        scores[model.score_category] = model.score

    return scores


def can_user_review_category(user_id, score_category):
    """Checks if user can review suggestions in category score_category.
    If the user has score above the minimum required score, then the user is
    allowed to review.

    Args:
        user_id: str. The id of the user.
        score_category: str. The category to check the user's score.

    Returns:
        bool. Whether the user can review suggestions under category
        score_category.
    """
    user_scoring = get_user_scoring(user_id, score_category)
    return user_scoring.can_user_review_category()


def get_all_user_ids_who_are_allowed_to_review(score_category):
    """Gets all user_ids of users who are allowed to review (as per their
    scores) suggestions to a particular category.

    Args:
        score_category: str. The category of the suggestion.

    Returns:
        list(str). All user_ids of users who are allowed to review in the given
        category.
    """
    return [
        model.user_id for model in user_models.UserContributionScoringModel
        .get_all_users_with_score_above_minimum_for_category(score_category)
    ]


def get_user_scoring(user_id, score_category):
    """Gets the user scoring model from storage and creates the
    corresponding user scoring domain object if the model exists. If the model
    does not exist a user scoring domain object with the given user_id and
    score category is created with the initial score and email values.

    Args:
        user_id: str. The id of the user.
        score_category: str. The category of the suggestion.

    Returns:
        UserContributionScoring. The user scoring object.
    """
    user_scoring_model = user_models.UserContributionScoringModel.get(
        user_id, score_category)

    if user_scoring_model:
        return get_user_scoring_from_model(user_scoring_model)

    return user_domain.UserContributionScoring(
        user_id, score_category, 0, False)


def check_can_resubmit_suggestion(suggestion_id, user_id):
    """Checks whether the given user can resubmit the suggestion.

    Args:
        suggestion_id: str. The ID of the suggestion.
        user_id: str. The ID of the user.

    Returns:
        bool. Whether the user can resubmit the suggestion.
    """

    suggestion = get_suggestion_by_id(suggestion_id)

    return suggestion.author_id == user_id


def _get_voiceover_application_class(target_type):
    """Returns the voiceover application class for a given target type.

    Args:
        target_type: str. The target type of the voiceover application.

    Returns:
        class. The voiceover application class for the given target type.

    Raises:
        Exception. The voiceover application target type is invalid.
    """
    target_type_to_classes = (
        suggestion_registry.VOICEOVER_APPLICATION_TARGET_TYPE_TO_DOMAIN_CLASSES)
    if target_type in target_type_to_classes:
        return target_type_to_classes[target_type]
    else:
        raise Exception(
            'Invalid target type for voiceover application: %s' % target_type)


def get_voiceover_application(voiceover_application_id):
    """Returns the BaseVoiceoverApplication object for the give
    voiceover application model object.

    Args:
        voiceover_application_id: str. The ID of the voiceover application.

    Returns:
        BaseVoiceoverApplication. The domain object out of the given voiceover
        application model object.
    """
    voiceover_application_model = (
        suggestion_models.GeneralVoiceoverApplicationModel.get_by_id(
            voiceover_application_id))
    voiceover_application_class = _get_voiceover_application_class(
        voiceover_application_model.target_type)
    return voiceover_application_class(
        voiceover_application_model.id,
        voiceover_application_model.target_id,
        voiceover_application_model.status,
        voiceover_application_model.author_id,
        voiceover_application_model.final_reviewer_id,
        voiceover_application_model.language_code,
        voiceover_application_model.filename,
        voiceover_application_model.content,
        voiceover_application_model.rejection_message)


def update_suggestions_with_math_svgs(suggestions_raw_latex_to_image_data_dict):
    """Saves an SVG for each LaTeX string without an SVG in suggestions
    and updates the suggestions.

    TODO(#10045): Remove this function once all the math-rich text components in
    suggestions have a valid math SVG stored in the datastore.

    Args:
        suggestions_raw_latex_to_image_data_dict:
            dict(str, dict(str, LatexStringSvgImageData)). The dictionary
            having the key as a suggestion ID each value as dict with key as
            a LaTeX string and its value as LatexStringSvgImageData domain
            object.

    Raises:
        Exception. If any of the SVG images provided fail validation.
    """
    suggestions_to_update = []
    suggestion_ids = suggestions_raw_latex_to_image_data_dict.keys()
    suggestion_models_to_update = (
        suggestion_models.GeneralSuggestionModel.get_multi(suggestion_ids))

    for suggestion_model in suggestion_models_to_update:
        suggestion = get_suggestion_from_model(suggestion_model)
        suggestion_id = suggestion.suggestion_id
        exp_id = suggestion.target_id
        raw_latex_to_image_data_dict = (
            suggestions_raw_latex_to_image_data_dict[suggestion_id])
        add_svg_filenames_for_latex_strings_in_html_string = (
            functools.partial(
                html_validation_service.
                add_svg_filenames_for_latex_strings_in_html_string,
                raw_latex_to_image_data_dict))
        suggestion.convert_html_in_suggestion_change(
            add_svg_filenames_for_latex_strings_in_html_string)
        updated_html_string = ''.join(suggestion.get_all_html_content_strings())
        filenames_mapping = (
            html_validation_service.
            extract_svg_filename_latex_mapping_in_math_rte_components(
                updated_html_string))

        for filename, raw_latex in filenames_mapping:
            # Some new filenames may already have the images saved from the math
            # rich-text editor, for these files we don't need to save the image
            # again.
            if raw_latex in raw_latex_to_image_data_dict.keys():
                image_file = raw_latex_to_image_data_dict[raw_latex].raw_image
                image_validation_error_message_suffix = (
                    'SVG image provided for latex %s failed validation' % (
                        raw_latex))
                try:
                    file_format = (
                        image_validation_services.validate_image_and_filename(
                            image_file, filename))
                except utils.ValidationError as e:
                    e = '%s %s' % (e, image_validation_error_message_suffix)
                    raise Exception(e)
                image_is_compressible = (
                    file_format in feconf.COMPRESSIBLE_IMAGE_FORMATS)
                fs_services.save_original_and_compressed_versions_of_image(
                    filename, feconf.ENTITY_TYPE_EXPLORATION, exp_id,
                    image_file, 'image', image_is_compressible)

        suggestions_to_update.append(suggestion)
    _update_suggestions(suggestions_to_update, update_last_updated_time=False)


def get_latex_strings_to_suggestion_ids_mapping():
    """Returns a batch of LaTeX strings from suggestion which have LaTeX
    strings without SVGs.

    TODO(#10045): Remove this function once all the math-rich text components in
    suggestions have a valid math SVG stored in the datastore.

    Returns:
        dict(str, list(str)). The dict having each key as an suggestion iD and
        value as a list of LaTeX string. Each list has all the LaTeX
        strings in that particular suggestion.
    """

    exploration_suggestion_models = (
        suggestion_models.GeneralSuggestionModel.get_all().filter(
            suggestion_models.GeneralSuggestionModel.
            target_type == suggestion_models.TARGET_TYPE_EXPLORATION).filter(
                suggestion_models.GeneralSuggestionModel.
                suggestion_type == (
                    suggestion_models.SUGGESTION_TYPE_EDIT_STATE_CONTENT)))
    suggestion_id_to_latex_strings = {}
    for model in exploration_suggestion_models:
        suggestion = get_suggestion_from_model(model)
        html_string = ''.join(suggestion.get_all_html_content_strings())
        latex_strings_without_svg = (
            html_validation_service.
            get_latex_strings_without_svg_from_html(html_string))
        if len(latex_strings_without_svg) > 0:
            suggestion_id_to_latex_strings[suggestion.suggestion_id] = (
                latex_strings_without_svg)

    return suggestion_id_to_latex_strings
