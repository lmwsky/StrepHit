from __future__ import absolute_import

import yaml
import re
import os
import sys


class DateNormalizer(object):
    """
    find matches in text strings using regular expressions and transforms them
    according to a pattern transformation expression evaluated on the match

    the specifications are given in yaml format and allow to define meta functions
    and meta variables as well as the pattern and transformation rules themselves.

    meta variables will be placed inside patterns which use them in order to
    make writing patterns easier. meta variables will be available to use from
    inside the meta functions too as a dictionary named meta_vars

    a pattern transformation expression is an expression which will be evaluated
    if the corresponding regular expression matches. the pattern transformation
    will have access to all the meta functions and meta variables defined and
    to a variable named 'match' containing the regex match found
    """

    def __init__(self, language):
        path = os.path.join(os.path.dirname(__file__), 'resources',
                            'normalization_rules_%s.yml' % language)
        with open(path) as f:
            specs = yaml.load(f)

        self._meta_init(specs)
        basic_r = {name: pattern for name, pattern in self.meta_vars.iteritems()}

        self.regexes = {}
        for category, regexes in specs.iteritems():
            regexes = sum((x.items() for x in regexes), [])
            self.regexes[category] = [(re.compile(pattern.replace(' ', '\\s*') \
                                                         .format(**basic_r),
                                                  re.IGNORECASE), result)
                                      for pattern, result in regexes]

    def _meta_init(self, specs):
        """ Reads the meta variables and the meta functions from the specification

        :param dict specs: The specifications loaded from the file
        :return: None
        """
        self.meta_vars = specs.pop('__meta_vars__')

        # compile meta functions in a dictionary
        self.meta_funcs = {}
        for f in specs.pop('__meta_funcs__'):
            exec f in self.meta_funcs

        # make meta variables available to the meta functions just defined
        self.meta_funcs['__builtins__']['meta_vars'] = self.meta_vars

        self.globals = self.meta_funcs
        self.globals.update(self.meta_vars)

    def normalize_one(self, expression, conflict='first'):
        """ Find the matching part in the given expression

        :param str expression: The expression in which to search the match
        :param str conflict: Whether to return the first match found or scan
         through all the provided regular expressions and return the longest
         or shortest part of the string matched by a regular expression.
         Note that the match will always be the first one found in the string,
         this parameter tells how to resolve conflicts when there is more than
         one regular expression that returns a match. When more matches have
         the same length the first one found counts
         Allowed values are `first`, `longest` and `shortest`
        :return: Tuple with (start, end), category, result
        :rtype: tuple
        """

        best_match = None
        expression = expression.lower()
        for category, regexes in self.regexes.iteritems():
            for regex, transform in regexes:
                match = regex.search(expression)
                if not match:
                    continue
                elif conflict == 'first':
                    return self._process_match(category, transform, match, 0)
                elif best_match is None or \
                        conflict == 'longest' and match.end() - match.start() > best_match[1] or \
                        conflict == 'shortest' and match.end() - match.start() < best_match[1]:
                    best_match = match, match.end() - match.start(), category, transform

        if best_match is None:
            return (-1, -1), None, None
        else:
            match, _, category, transform = best_match
            return self._process_match(category, transform, match, 0)

    def normalize_many(self, expression):
        """ Find all the matching entities in the given expression expression

        :param str expression: The expression in which to look for
        :return: Generator of tuples (start, end), category, result
        """
        expression = expression.lower()
        position = 0  # start matching from here, and move forward as new matches
                      # are found so to avoid overlapping matches and return
                      # the correct offset inside the original sentence

        for category, regexes in self.regexes.iteritems():
            for regex, transform in regexes:
                end = 0
                for match in regex.finditer(expression[position:]):
                    yield self._process_match(category, transform, match, position)
                    end = max(end, match.end())
                position += end

    def _process_match(self, category, transform, match, first_position):
        result = eval(transform, self.globals, {'match': match})
        start, end = match.span()
        return (first_position + start, first_position + end), category, result


def normalize_numerical_fes(normalizer, sentence_id, tokens):
    """ Normalize numerical FEs in a sentence such as dates, durations, etc

    :param DateNormalizer normalizer: normalizer to use
    :param str sentence_id: The ID of the sentence currently processed
    :param list tokens: The list of tokens of the sentence, containing POS tag,
     frame and IOB tag information
    :return: The list of tokens with normalized results
    :rtype: list
    """
    sentence = ' '.join(x[2] for x in tokens)

    for (start, end), _, _ in normalizer.normalize_many(sentence):
        original = sentence[start:end]

        # find the first token of the match
        cursor = i = 0
        while cursor != start and i < len(tokens):
            cursor += len(tokens[i][2]) + 1  # remember the space between tokens
            i += 1

        if i == len(tokens):
            continue  # the normalized token is a sub-token of another token

        # find the last token of the match
        j = i + 1
        while ' '.join(x[2] for x in tokens[i:j]) != original and j < len(tokens):
            j += 1

        if j == len(tokens):
            continue  # the normalized token is a sub-token of another token

        # find an appropriate tag (i.e. anything different from 'O'
        # if exists among the matching tokens)
        tags = set(x[-1] for x in tokens[i:j] if x[-1] != 'O')
        assert len(tags) in {0, 1}, 'Cannot decide which tag to use for %s: %r' % (
                                    original, tags)
        tag = tags.pop() if tags else 'O'

        # replace the old tokens with a new one
        tokens = (tokens[:i] +
                  [[sentence_id, '-', original, 'ENT', original, tokens[0][-2], tag]] +
                  tokens[j:])
        assert ' '.join(x[2] for x in tokens) == sentence, 'Failed to rebuild sentence'

    return tokens