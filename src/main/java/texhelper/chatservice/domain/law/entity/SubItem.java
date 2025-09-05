package texhelper.chatservice.domain.law.entity;

import jakarta.persistence.*;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Getter;
import lombok.NoArgsConstructor;
import texhelper.chatservice.common.entity.Timestamped;

@Getter
@Entity
@Table(name = "subItem")
@NoArgsConstructor
@AllArgsConstructor
@Builder
    /**
     *   https://open.law.go.kr/LSO/openApi/guideResult.do
     *   호에 속한 목을 subItem으로 분리하에 테이블을 만들었습니다.
     */
public class SubItem extends Timestamped {
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    @Column
    private Long subItemId;

    @Column
    private String subItemNo;

    @Column(columnDefinition = "text")
    private String subContent;

    @ManyToOne
    @JoinColumn(name = "itemId", nullable = false)
    private Item item;

    public void assignItem(Item item) {
        this.item = item;
    }
}
