/**
 * Shared map of Google Group email address -> Drive folder ID to sync.
 * This file is maintained by multiple scripts — edit here only.
 *
 * Keying on the group's address (rather than deriving it from the
 * folder's name at sync time) lets an entry here override the usual
 * name-matching rules for a specific group/folder pair.
 *
 *   'group@example.org': '1AbCdEfGhIjKlMnOpQrStUvWxYz',
 */
const FOLDER_IDS = {
  'care-circle@berkeleymoshav.org': '19UXbSuyBsMchJ3-bYSrU_R4TiSTWbtYt',
  'community-life-circle@berkeleymoshav.org': '1lwU1NsHoTm6_GFXViaMjHo-QyQtinzld',
  'construction-interface-team@berkeleymoshav.org': '1gvt_woH6fgnGSinzcBVFwPqLq8cD7KGb',
  'coordinating-circle@berkeleymoshav.org': '1NlflXGdWU5lZoUZE_wJtp63RjYAC2x0t',
  'development-finance-and-legal-circle@berkeleymoshav.org': '1l-HurF1r1VwqaEFrWtak85o83AnE-jAv',
  'furnishings-working-group@berkeleymoshav.org': '1zrv5q3cthYQn6fy8jUB5RI4sYjPG2Fe4',
  'hiking-club@berkeleymoshav.org': '1RQZh0e3Op1meUDQ8Lp0UtLRA0-mWCSZc',
  'hoa-launch-work-group@berkeleymoshav.org': '1N6F1oCxRLTYokcZ-iXxKi4ac8OjOIkOu',
  'jewish-life-circle@berkeleymoshav.org': '1HFUj8-IJANJOEQTmhodY2PCJ_xQ0RxMl',
  'landscaping-working-group@berkeleymoshav.org': '1TkOjo3t6GxylJvWOO94bY04wkbjI3cPk',
  'maintenance-circle@berkeleymoshav.org': '10lQclCFeK0z9mm4vju_9ouIEnPci70PX',
  'meals-circle@berkeleymoshav.org': '1s5gARw-PSNdXJ_kA1lVjp22-rioE4JCi',
  'membership-circle@berkeleymoshav.org': '1IUgNgY72c3TWN4CB9RlaJyubVNYoYJuS',
  'parking-and-car-share-circle@berkeleymoshav.org': '1GlAyjUJWYbQhtAi2HEFouZ4hlcHMkuEH',
  'process-and-governance-circle@berkeleymoshav.org': '1wnVv0f34MV2OrestlyDMVTkUqAbxZ7b0',
  'rentals-and-resales-circle@berkeleymoshav.org': '1mA2BlZzK-aghrVxq34Jt0709v46L-dJG',
  'social-gatherings-circle@berkeleymoshav.org': '1yhvWcSHj279xwKiNpS-zXBVyh9doIGyE',
  'technology-circle@berkeleymoshav.org': '15XV79dE2bl9DAVj8vg9AhBZqhGdGTrcF',
  'young-families@berkeleymoshav.org': '1oNdZ5a_Rc1ro4qMyTAHJm20ozvlpDFpf',
};
