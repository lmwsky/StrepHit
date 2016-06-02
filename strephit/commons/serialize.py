#!/usr/bin/env python
# -*- encoding: utf-8 -*-
from __future__ import absolute_import

import logging
import json

import click

from strephit.commons import wikidata, parallel
from strephit.commons.date_normalizer import normalize_numerical_fes

logger = logging.getLogger(__name__)


class ClassificationSerializer:
    def __init__(self, fe_to_wid, url_to_wid, language, subject_fes):
        self.fe_to_wid = fe_to_wid
        self.url_to_wid = url_to_wid
        self.language = language
        self.subject_fes = subject_fes

    def get_subject(self, data):
        """ Returns the wikidata id of the subject of the statements
        """

        # first, try to see if there is one (and exactly one) FE of one of the
        # types that can be subjects
        candidates = [fe for fe in data['fes'] if fe['fe'] in self.subject_fes]
        if len(candidates) == 1:
            name = candidates[0]['chunk']
            wid = wikidata.resolver_with_hints(
                'P1559', name, self.language
            )
        # if this fails, assume the subject is the main subject of the article
        # from which this sentence was extracted
        elif data['url'] in self.url_to_wid:
            name = None
            wid = self.url_to_wid[data['url']]
        else:
            name = data.get('name')
            wid = wikidata.resolver_with_hints('P1559', name, self.language) or None if name else None

        return name, wid

    def serialize_numerical(self, subj, fe, url):
        """ Serializes a numerical FE found by the normalizer
        """
        literal = fe['literal']
        if fe['fe'] == 'Time':
            value = wikidata.format_date(**literal)
            yield wikidata.finalize_statement(subj, 'P585', value, self.language, url,
                                              resolve_property=False, resolve_value=False)
        elif fe['fe'] == 'Duration':
            if 'start' in literal:
                value = wikidata.format_date(**literal['start'])
                yield wikidata.finalize_statement(subj, 'P580', value, self.language, url,
                                                  resolve_property=False, resolve_value=False)

            if 'end' in literal:
                value = wikidata.format_date(**literal['end'])
                yield wikidata.finalize_statement(subj, 'P580', value, self.language, url,
                                                  resolve_property=False, resolve_value=False)

    def to_statements(self, data, input_encoded=True):
        """ Converts the classification results into quick statements
        """
        data = json.loads(data) if input_encoded else data

        url = data.get('url')
        if not url:
            logger.warn('skipping item without url')
            return

        name, subj = self.get_subject(data)
        if not subj:
            logger.warn('could not resolve wikidata id of subject "%s", skipping sentence', name)
            return

        # if not already done, normalize numerica FEs
        if not any(fe['fe'] in ['Time', 'Duration'] for fe in data['fes']):
            data['fes'].extend(normalize_numerical_fes(self.language, data['sentence']))

        for fe in data['fes']:
            if fe['fe'] in ['Time', 'Duration']:
                for each in self.serialize_numerical(subj, fe, url):
                    yield each
            else:
                prop = self.fe_to_wid.get(fe['fe'])
                if prop:
                    yield wikidata.finalize_statement(subj, prop, fe['chunk'], self.language, url,
                                                      resolve_property=False, resolve_value=True)
                else:
                    logger.debug('unknown fe type %s, skipping', fe['fe'])
                    continue


def map_url_to_wid(semistructured):
    """ Read the quick statements generated from the semi structured data
        and build a map associating url to wikidata id
    """

    # urls are not primary keys, so skip urls with more than one subject
    banned_urls = set()

    url_to_wid = {}
    for row in semistructured:
        parts = row[:-1].split('\t')
        wid, url = parts[0], parts[-1]
        if url in url_to_wid and url_to_wid[url] != wid:
            url_to_wid.pop(url)
            banned_urls.add(url)
        elif url not in banned_urls:
            url_to_wid[parts[-1]] = parts[0]

    return url_to_wid


@click.command()
@click.argument('classified', type=click.File('r'))
@click.argument('frame-data', type=click.File('r'))
@click.argument('output', type=click.File('w'))
@click.argument('language')
@click.option('--semistructured', type=click.File('r'))
@click.option('--processes', '-p', default=0)
def main(classified, frame_data, output, language, semistructured, processes):
    """ Serialize classification results into quickstatements
    """

    if semistructured:
        url_to_wid = map_url_to_wid(semistructured)
        logger.info('used semi structured dataset to infer %d wikidata ids',
                    len(url_to_wid))
    else:
        url_to_wid = {}
        logger.info('TIP: using the semi structured dataset could help in '
                    'resolving the wikidata id of more subjects')

    frame_data = json.load(frame_data)
    fe_to_wid = {}
    for data in frame_data.values():
        for fe in data.get('core_fes', []) + data.get('extra_fes', []):
            if 'id' in fe:
                fe_to_wid[fe['fe']] = fe['id']
            else:
                logger.warn('dropping FE %s because no wikidata property is specified',
                            fe['fe'])

    # these FEs can act as subject of the statements produced from a frame
    # if none of these  (or more than one) is found, then the subject is
    # taken to be the subject of the article from which the sentence was extracted
    subject_fes = {u'Agent',
                   u'Author',
                   u'Elected_person',
                   u'Entity',
                   u'Exhibitor',
                   u'Individual',
                   u'New_member',
                   u'Participant',
                   u'Player',
                   u'Producer',
                   u'Visitor'}

    count = 0
    serializer = ClassificationSerializer(fe_to_wid, url_to_wid, language, subject_fes)
    for statement in parallel.map(serializer.to_statements, classified,
                                  processes=processes, flatten=True):
        if statement:
            output.write(statement)
            output.write('\n')

            count += 1
            if count % 1000 == 0:
                logger.info('produced %d statements', count)

    logger.info('Done, produced %d statements', count)